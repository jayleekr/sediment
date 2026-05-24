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
}

#[derive(Debug, Deserialize)]
pub struct Asset {
    pub name: String,
    pub url: String, // api.github.com URL — required for private-repo downloads
    /// GitHub sets this on every asset upload — "sha256:HEX". Saves us a
    /// separate sidecar fetch.
    #[serde(default)]
    pub digest: Option<String>,
}

#[derive(Debug)]
pub struct UpdateInfo {
    pub current: String,
    pub latest: String,
    pub release_url: String,
    pub asset_url: Option<String>, // api.github.com URL for download (private-repo safe)
    pub asset_name: Option<String>,
    pub asset_digest: Option<String>, // "sha256:HEX" from GitHub's asset metadata
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
    let url = format!(
        "https://api.github.com/repos/{}/{}/releases/latest",
        OWNER, REPO
    );
    let mut req = client
        .get(&url)
        .header("Accept", "application/vnd.github+json");
    // The Sediment repo is private — anonymous GH API calls 404 against
    // /releases/latest. We forward GITHUB_TOKEN if the user has one set
    // (a PAT with `repo` scope works; `gh auth token` produces a usable
    // one). Without a token, users see a clear hint instead of a bare 404.
    if let Ok(tok) = std::env::var("GITHUB_TOKEN") {
        if !tok.is_empty() {
            req = req.bearer_auth(tok);
        }
    }
    let resp = req.send().await.with_context(|| format!("GET {}", url))?;
    if resp.status().as_u16() == 404 {
        return Err(anyhow!(
            "GitHub API returned 404 for releases/latest. \
             Either the repo is private and you need to set GITHUB_TOKEN \
             (try: `export GITHUB_TOKEN=$(gh auth token)`), \
             or no release has been published yet."
        ));
    }
    let r: Release = resp.error_for_status()?.json().await?;

    // Parse "sediment-cli-vX.Y.Z" → X.Y.Z
    let latest = r.tag_name.trim_start_matches("sediment-cli-v").to_string();
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
        // Use the api.github.com asset URL (`url` field) rather than the
        // browser_download_url. For private repos, GitHub serves asset bytes
        // ONLY through the API endpoint with Authorization + an explicit
        // `Accept: application/octet-stream` header. browser_download_url
        // 404s without a session cookie.
        asset_url: asset.map(|a| a.url.clone()),
        asset_name: asset.map(|a| a.name.clone()),
        asset_digest: asset.and_then(|a| a.digest.clone()),
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
    let tarball_path = tmpdir
        .path()
        .join(info.asset_name.as_deref().unwrap_or("sediment.tar.gz"));
    let mut req = client
        .get(asset_url)
        // For api.github.com asset URL, this header tells GitHub to serve
        // the raw bytes (not the JSON asset metadata).
        .header("Accept", "application/octet-stream");
    // Private-repo asset download also needs the PAT.
    if let Ok(tok) = std::env::var("GITHUB_TOKEN") {
        if !tok.is_empty() {
            req = req.bearer_auth(tok);
        }
    }
    let mut resp = req.send().await?.error_for_status()?;
    let mut file = fs::File::create(&tarball_path)?;
    while let Some(chunk) = resp.chunk().await? {
        file.write_all(&chunk)?;
    }
    file.flush()?;
    drop(file);

    // Verify sha256 against the digest GitHub records for the uploaded
    // asset. No separate sidecar fetch needed — the field comes from the
    // /releases/latest response.
    if let Some(expected_full) = &info.asset_digest {
        let expected = expected_full
            .strip_prefix("sha256:")
            .unwrap_or(expected_full);
        let actual = sha256_file(&tarball_path)?;
        if !expected.eq_ignore_ascii_case(&actual) {
            return Err(anyhow!(
                "sha256 mismatch: expected={} actual={}",
                expected,
                actual
            ));
        }
    }

    // Extract tarball with path-traversal protection.
    //
    // 2026-05-23 FIX-D (REPORT.md CRIT #8): previously this shelled out to
    // `tar -xzf` which accepts any path inside the archive — a malicious
    // tarball with `../../usr/local/bin/curl` would replace system binaries
    // outside extract_dir. The sha256 check only protects transport, not a
    // compromised publisher. Now we extract in-process via the `tar` crate
    // and reject any entry whose path escapes extract_dir.
    let extract_dir = tmpdir.path().join("extracted");
    fs::create_dir(&extract_dir)?;
    {
        use flate2::read::GzDecoder;
        use tar::Archive;
        let tarball_file = fs::File::open(&tarball_path)
            .with_context(|| format!("opening {}", tarball_path.display()))?;
        let mut archive = Archive::new(GzDecoder::new(tarball_file));
        // Per-entry validation. The `tar` crate's Entry::path() returns a
        // sanitized path, but we re-check against the destination after
        // joining to absolutely guarantee no escape via "..", absolute
        // paths, or symlinks pointing outside extract_dir.
        let extract_dir_canon = fs::canonicalize(&extract_dir)
            .with_context(|| format!("canonicalize {}", extract_dir.display()))?;
        for entry_result in archive.entries()? {
            let mut entry = entry_result.context("reading tar entry")?;
            let path_in_tar = entry.path().context("reading tar entry path")?.into_owned();
            // Reject absolute paths and any path containing "..".
            if path_in_tar.is_absolute() {
                return Err(anyhow!(
                    "tarball contains absolute path entry: {} — refusing to extract",
                    path_in_tar.display()
                ));
            }
            for component in path_in_tar.components() {
                if matches!(component, std::path::Component::ParentDir) {
                    return Err(anyhow!(
                        "tarball entry escapes extract dir: {} — refusing to extract",
                        path_in_tar.display()
                    ));
                }
            }
            // Compute the on-disk target + ensure it stays under extract_dir.
            let dest = extract_dir.join(&path_in_tar);
            // Resolve dest's parent (must exist or be creatable inside extract_dir).
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent).with_context(|| {
                    format!("creating dir {}", parent.display())
                })?;
                let parent_canon = fs::canonicalize(parent).with_context(|| {
                    format!("canonicalize {}", parent.display())
                })?;
                if !parent_canon.starts_with(&extract_dir_canon) {
                    return Err(anyhow!(
                        "tar entry would write outside extract dir: {} → {}",
                        path_in_tar.display(),
                        parent_canon.display()
                    ));
                }
            }
            // Reject symlinks entirely — a release tarball has no business
            // including them, and they're another escape vector.
            let entry_type = entry.header().entry_type();
            if entry_type.is_symlink() || entry_type.is_hard_link() {
                return Err(anyhow!(
                    "tarball contains symlink/hardlink: {} — refusing to extract",
                    path_in_tar.display()
                ));
            }
            entry
                .unpack(&dest)
                .with_context(|| format!("unpacking {}", dest.display()))?;
        }
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
