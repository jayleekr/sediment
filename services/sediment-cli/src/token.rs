// OS keychain storage for per-account JWTs.
//
// Service name: "sediment-cli"
// Username (keyring key): the account email
//
// On macOS this lands in Login Keychain; on Linux in Secret Service
// (gnome-keyring / kwallet); on Windows in Credential Manager.
//
// Tests run against an in-memory mock — see token_tests.rs.
#![allow(clippy::items_after_test_module)]

use anyhow::{Context, Result};

#[allow(dead_code)]
const SERVICE: &str = "sediment-cli";
#[allow(dead_code)]
const ACCOUNT_INDEX_KEY: &str = "_index";

#[cfg(not(test))]
mod backend {
    use anyhow::{Context, Result};
    use keyring::Entry;

    pub fn entry(account: &str) -> Result<Entry> {
        Entry::new(super::SERVICE, account).context("opening keychain entry")
    }

    pub fn get(account: &str) -> Result<Option<String>> {
        match entry(account)?.get_password() {
            Ok(t) => Ok(Some(t)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    pub fn set(account: &str, token: &str) -> Result<()> {
        entry(account)?
            .set_password(token)
            .context("writing keychain entry")
    }

    pub fn delete(account: &str) -> Result<()> {
        match entry(account)?.delete_credential() {
            Ok(()) => Ok(()),
            Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.into()),
        }
    }
}

#[cfg(test)]
mod backend {
    use anyhow::Result;
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};

    fn store() -> &'static Mutex<HashMap<String, String>> {
        static S: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();
        S.get_or_init(|| Mutex::new(HashMap::new()))
    }

    pub fn get(account: &str) -> Result<Option<String>> {
        Ok(store().lock().unwrap().get(account).cloned())
    }
    pub fn set(account: &str, token: &str) -> Result<()> {
        store().lock().unwrap().insert(account.into(), token.into());
        Ok(())
    }
    pub fn delete(account: &str) -> Result<()> {
        store().lock().unwrap().remove(account);
        Ok(())
    }
}

pub fn get(account: &str) -> Result<Option<String>> {
    backend::get(account)
}

pub fn set(account: &str, token: &str) -> Result<()> {
    backend::set(account, token)?;
    // Maintain a sidecar account-index so `auth list` is fast and doesn't
    // require scanning the keychain (which most backends don't allow).
    add_to_index(account)
}

pub fn delete(account: &str) -> Result<()> {
    backend::delete(account)?;
    remove_from_index(account)
}

// ---- account index sidecar ----
//
// The OS keychain has no portable "list all entries for service X" call.
// We keep a newline-delimited list in the config dir to enumerate accounts.

fn index_path() -> Result<std::path::PathBuf> {
    let dir = crate::config::config_dir()?;
    Ok(dir.join("accounts"))
}

fn read_index() -> Result<Vec<String>> {
    let path = index_path()?;
    if !path.exists() {
        return Ok(Vec::new());
    }
    let body =
        std::fs::read_to_string(&path).with_context(|| format!("reading {}", path.display()))?;
    Ok(body
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(String::from)
        .collect())
}

fn write_index(items: &[String]) -> Result<()> {
    let path = index_path()?;
    std::fs::write(&path, items.join("\n")).with_context(|| format!("writing {}", path.display()))
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

pub fn list_accounts() -> Result<Vec<String>> {
    read_index()
}

pub fn _ignore_unused() {
    let _ = ACCOUNT_INDEX_KEY;
}
