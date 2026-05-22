// Self-update via GitHub Releases.
//
// Flow:
//   1. GET https://api.github.com/repos/{OWNER}/{REPO}/releases/latest
//   2. Parse tag (e.g. "sediment-cli-v0.1.2") → version
//   3. Compare against env!("CARGO_PKG_VERSION")
//   4. If newer (or --force), find asset matching this binary's target triple
//   5. Download tarball + sha256 sidecar; verify
//   6. Untar to a tempdir, atomic-rename over the running binary
//
// No telemetry. No background daemon. The user explicitly runs `sediment update`.
// Hosts: api.github.com + github.com/<release-assets>; both HTTPS.

use anyhow::{anyhow, Context, Result};
use reqwest::Client;
use serde::Deserialize;
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

const OWNER: &str = "jayleekr";
const REPO: &str = "sediment";
const USER_AGENT: &str = concat!("sediment-cli-updater/", env!("CARGO_PKG_VERSION"));

#[derive(Debug, Deserialize)]
pub struct Release {
    pub tag_name: String,
    pub html_url: String,
    pub assets: Vec<Asset>,
    #[serde(default)]
    pub prerelease: bool,
}

#[derive(Debug, Deserialize)]
pub struct Asset {
    pub name: String,
    pub browser_download_url: String,
    pub size: u64,
}

#[derive(Debug)]
pub struct UpdateInfo {
    pub current: String,
    pub latest: String,
    pub release_url: String,
    pub asset_url: Option<String>,
    pub asset_name: Option<String>,
    pub is_newer: bool,
}

pub fn target_triple() -> &'static str {
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    return "aarch64-apple-darwin";
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    return "x86_64-apple-darwin";
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    return "x86_64-unknown-linux-gnu";
    #[cfg(all(target_os = "linux", target_arch = "aarch64"))]
    return "aarch64-unknown-linux-gnu";
    #[cfg(target_os = "windows")]
    return "x86_64-pc-windows-msvc";
    #[allow(unreachable_code)]
    ""
}

pub async fn check_latest() -> Result<UpdateInfo> {
    let client = Client::builder()
        .user_agent(USER_AGENT)
        .timeout(std::time::Duration::from_secs(20))
        .build()?;
    let url = format!("https://api.github.com/repos/{}/{}/releases/latest", OWNER, REPO);
    let r: Release = client
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .with_context(|| format!("GET {}", url))?
        .error_for_status()?
        .json()
        .await?;

    // Parse "sediment-cli-vX.Y.Z" → X.Y.Z
    let latest = r
        .tag_name
        .trim_start_matches("sediment-cli-v")
        .to_string();
    let current = env!("CARGO_PKG_VERSION").to_string();
    let is_newer = compare_semver(&latest, &current) > 0;

    // Find asset for this triple. Asset naming convention:
    //   sediment-{TRIPLE}.tar.gz
    let triple = target_triple();
    let want = format!("sediment-{}.tar.gz", triple);
    let asset = r.assets.iter().find(|a| a.name == want);

    Ok(UpdateInfo {
        current,
        latest,
        release_url: r.html_url,
        asset_url: asset.map(|a| a.browser_download_url.clone()),
        asset_name: asset.map(|a| a.name.clone()),
        is_newer,
    })
}

pub async fn perform_update(info: &UpdateInfo) -> Result<PathBuf> {
    let asset_url = info
        .asset_url
        .as_ref()
        .ok_or_else(|| anyhow!("no release asset for target {}", target_triple()))?;

    let client = Client::builder()
        .user_agent(USER_AGENT)
        .timeout(std::time::Duration::from_secs(120))
        .build()?;

    // Download tarball into tempdir
    let tmpdir = tempfile::tempdir().context("creating tempdir for update")?;
    let tarball_path = tmpdir.path().join(info.asset_name.as_deref().unwrap_or("sediment.tar.gz"));
    let mut resp = client
        .get(asset_url)
        .send()
        .await?
        .error_for_status()?;
    let mut file = fs::File::create(&tarball_path)?;
    while let Some(chunk) = resp.chunk().await? {
        file.write_all(&chunk)?;
    }
    file.flush()?;
    drop(file);

    // (Optional) fetch + verify sha256 sidecar — skip if missing (some releases
    // may publish only the tarball). The asset name + .sha256 is the convention.
    let sha_url = format!("{}.sha256", asset_url);
    let sha_resp = client.get(&sha_url).send().await;
    if let Ok(r) = sha_resp {
        if r.status().is_success() {
            let body = r.text().await.unwrap_or_default();
            // sha256 sidecar is "HEXDIGEST  filename"
            if let Some(expected) = body.split_whitespace().next() {
                let actual = sha256_file(&tarball_path)?;
                if !expected.eq_ignore_ascii_case(&actual) {
                    return Err(anyhow!(
                        "sha256 mismatch: expected={} actual={}",
                        expected, actual
                    ));
                }
            }
        }
    }

    // Extract tarball
    let extract_dir = tmpdir.path().join("extracted");
    fs::create_dir(&extract_dir)?;
    let extract_status = std::process::Command::new("tar")
        .args(["-xzf", tarball_path.to_str().unwrap()])
        .arg("-C")
        .arg(&extract_dir)
        .status()?;
    if !extract_status.success() {
        return Err(anyhow!("tar -xzf failed"));
    }

    // The tarball convention: one binary named "sediment" at the archive root.
    let new_bin = extract_dir.join("sediment");
    if !new_bin.exists() {
        return Err(anyhow!(
            "release tarball did not contain a `sediment` binary at the root"
        ));
    }
    // Preserve executable bit
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&new_bin, fs::Permissions::from_mode(0o755))?;
    }

    // Atomic swap onto the running binary path.
    let current_bin = env::current_exe().context("resolving current executable path")?;
    swap_binary(&new_bin, &current_bin)?;

    // tmpdir auto-deletes on drop.
    Ok(current_bin)
}

/// Atomic-rename `new_bin` over `target`. Falls back to copy+chmod when rename
/// fails (e.g. cross-device move from /tmp to /usr/local/bin).
fn swap_binary(new_bin: &Path, target: &Path) -> Result<()> {
    // On Unix, you can replace a running executable's file while it's still mapped.
    // The OS holds the old inode open until the process exits; new lookups get
    // the new file. So rename() over the path is safe.
    match fs::rename(new_bin, target) {
        Ok(()) => Ok(()),
        Err(_) => {
            // Cross-device — fall back to copy.
            fs::copy(new_bin, target).context("copying new binary into place")?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(target, fs::Permissions::from_mode(0o755))?;
            }
            Ok(())
        }
    }
}

fn sha256_file(path: &Path) -> Result<String> {
    // Avoid pulling in a sha2 crate dep just for this; shell out to shasum/sha256sum.
    let candidates = ["sha256sum", "shasum"];
    let mut last_err: Option<anyhow::Error> = None;
    for cmd in &candidates {
        let out = std::process::Command::new(cmd)
            .args(if *cmd == "shasum" {
                vec!["-a", "256"]
            } else {
                vec![]
            })
            .arg(path)
            .output();
        match out {
            Ok(o) if o.status.success() => {
                let s = String::from_utf8_lossy(&o.stdout);
                if let Some(hex) = s.split_whitespace().next() {
                    return Ok(hex.to_string());
                }
            }
            Ok(_) => continue,
            Err(e) => {
                last_err = Some(e.into());
                continue;
            }
        }
    }
    Err(last_err.unwrap_or_else(|| anyhow!("no sha256 tool found")))
}

// Tiny semver-ish compare. Handles "X.Y.Z" with optional "-pre" suffix.
fn compare_semver(a: &str, b: &str) -> i32 {
    let split = |s: &str| -> (u32, u32, u32) {
        let core = s.split('-').next().unwrap_or(s);
        let mut parts = core.split('.').map(|p| p.parse::<u32>().unwrap_or(0));
        (
            parts.next().unwrap_or(0),
            parts.next().unwrap_or(0),
            parts.next().unwrap_or(0),
        )
    };
    let (ax, ay, az) = split(a);
    let (bx, by, bz) = split(b);
    let av = (ax as u64) << 32 | (ay as u64) << 16 | (az as u64);
    let bv = (bx as u64) << 32 | (by as u64) << 16 | (bz as u64);
    if av > bv {
        1
    } else if av < bv {
        -1
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn semver_compare() {
        assert_eq!(compare_semver("0.1.2", "0.1.1"), 1);
        assert_eq!(compare_semver("0.1.0", "0.1.1"), -1);
        assert_eq!(compare_semver("0.1.0", "0.1.0"), 0);
        assert_eq!(compare_semver("0.2.0", "0.1.99"), 1);
        assert_eq!(compare_semver("1.0.0-rc1", "1.0.0-rc2"), 0); // pre-release ignored
    }

    #[test]
    fn triple_is_set_for_this_host() {
        let t = target_triple();
        assert!(!t.is_empty(), "unsupported host platform");
    }
}
