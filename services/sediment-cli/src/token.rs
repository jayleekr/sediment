// Per-account JWT storage.
//
// **Storage backend**: file at `<config_dir>/credentials.json` with mode 0600.
//
// Why not OS keychain? macOS Keychain enforces a per-binary ACL that prompts
// "sediment wants to use your confidential information" on every read from a
// binary that wasn't on the ACL trust list — and that ACL list resets on
// every binary rebuild (path or codesign change). For a 24h dev JWT, the UX
// cost is severe. `gh`, `aws-cli`, and most peer tools use plain config-dir
// files with restrictive permissions. We do the same.
//
// **Layout**:
//   {
//     "accounts": {
//       "jay.lee@example.com": "eyJhbGciOiJI...",
//       "admin@example.com":   "eyJhbGciOiJI..."
//     }
//   }
//
// **Migration**: on first read with no file but with a legacy macOS keyring
// entry, we silently lift it over. Then we never touch the keychain again.
//
// **Security model**: 0600 file in user's home dir is equivalent to ~/.ssh
// keys, ~/.aws/credentials, ~/.config/gh/hosts.yml. The threat model assumes
// the user's home dir is private to them. Use `SEDIMENT_TOKEN` env override
// for ephemeral / CI runs that shouldn't touch disk.

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

#[allow(dead_code)]
const SERVICE: &str = "sediment-cli";

#[derive(Debug, Default, Serialize, Deserialize)]
struct Creds {
    #[serde(default)]
    accounts: BTreeMap<String, String>,
}

fn creds_path() -> Result<PathBuf> {
    Ok(crate::config::config_dir()?.join("credentials.json"))
}

fn load() -> Result<Creds> {
    let path = creds_path()?;
    if !path.exists() {
        return Ok(Creds::default());
    }
    let body = fs::read_to_string(&path)
        .with_context(|| format!("reading {}", path.display()))?;
    let creds: Creds = serde_json::from_str(&body).with_context(|| {
        format!("parsing {} (corrupted? back it up and re-login)", path.display())
    })?;
    Ok(creds)
}

fn save(creds: &Creds) -> Result<()> {
    let path = creds_path()?;
    let body = serde_json::to_string_pretty(creds)?;
    // Write to a temp file in the same dir, then atomic rename. Set perms
    // BEFORE rename so the live file never appears world-readable.
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, body).with_context(|| format!("writing {}", tmp.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600))?;
    }
    fs::rename(&tmp, &path)
        .with_context(|| format!("renaming {} → {}", tmp.display(), path.display()))?;
    Ok(())
}

pub fn get(account: &str) -> Result<Option<String>> {
    let mut creds = load()?;
    if let Some(v) = creds.accounts.get(account) {
        return Ok(Some(v.clone()));
    }
    // Migration: if a legacy keyring entry exists, lift it over silently.
    if let Some(legacy) = legacy_keychain_get(account)? {
        creds.accounts.insert(account.into(), legacy.clone());
        save(&creds)?;
        // Also try to clear the legacy entry so it doesn't keep prompting.
        let _ = legacy_keychain_delete(account);
        return Ok(Some(legacy));
    }
    Ok(None)
}

pub fn set(account: &str, token: &str) -> Result<()> {
    let mut creds = load()?;
    creds.accounts.insert(account.into(), token.into());
    save(&creds)?;
    add_to_index(account)
}

pub fn delete(account: &str) -> Result<()> {
    let mut creds = load()?;
    creds.accounts.remove(account);
    save(&creds)?;
    let _ = legacy_keychain_delete(account);
    remove_from_index(account)
}

pub fn list_accounts() -> Result<Vec<String>> {
    let creds = load()?;
    let mut out: Vec<String> = creds.accounts.keys().cloned().collect();
    out.sort();
    Ok(out)
}

// ============================================================
// Legacy keychain migration (read-once on first request after upgrade)
// ============================================================

#[cfg(target_os = "macos")]
fn legacy_keychain_get(account: &str) -> Result<Option<String>> {
    // Shell out to `security` rather than depend on the keyring crate just
    // for migration. We still get a prompt the first time because the
    // ACL trust list doesn't include this binary path — but the user only
    // sees it ONCE, on first read after upgrade. The token migrates and
    // future reads come from the file.
    let out = std::process::Command::new("security")
        .args([
            "find-generic-password", "-s", SERVICE, "-a", account, "-w",
        ])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if s.is_empty() {
                Ok(None)
            } else {
                Ok(Some(s))
            }
        }
        _ => Ok(None),
    }
}

#[cfg(target_os = "macos")]
fn legacy_keychain_delete(account: &str) -> Result<()> {
    let _ = std::process::Command::new("security")
        .args(["delete-generic-password", "-s", SERVICE, "-a", account])
        .output();
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn legacy_keychain_get(_account: &str) -> Result<Option<String>> {
    Ok(None)
}

#[cfg(not(target_os = "macos"))]
fn legacy_keychain_delete(_account: &str) -> Result<()> {
    Ok(())
}

// ============================================================
// Account-name index sidecar
// ============================================================
//
// Strictly, accounts are already keys in credentials.json — we could derive
// the list directly from there. The sidecar is kept for compatibility with
// the config::read_default_account fallback ("if exactly one account, use
// it as the default") which reads this file as cheap input.

fn index_path() -> Result<PathBuf> {
    Ok(crate::config::config_dir()?.join("accounts"))
}

fn read_index() -> Result<Vec<String>> {
    let path = index_path()?;
    if !path.exists() {
        return list_accounts();
    }
    let body = fs::read_to_string(&path)
        .with_context(|| format!("reading {}", path.display()))?;
    Ok(body
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(String::from)
        .collect())
}

fn write_index(items: &[String]) -> Result<()> {
    let path = index_path()?;
    fs::write(&path, items.join("\n"))
        .with_context(|| format!("writing {}", path.display()))
}

fn add_to_index(account: &str) -> Result<()> {
    let mut items = read_index().unwrap_or_default();
    if !items.iter().any(|a| a == account) {
        items.push(account.to_string());
        items.sort();
        write_index(&items)?;
    }
    Ok(())
}

fn remove_from_index(account: &str) -> Result<()> {
    let mut items = read_index().unwrap_or_default();
    let before = items.len();
    items.retain(|a| a != account);
    if items.len() != before {
        write_index(&items)?;
    }
    Ok(())
}

pub fn _ignore_unused() {
    let _ = anyhow!("");
    let _ = SERVICE;
}
