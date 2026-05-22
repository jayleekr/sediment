// Runtime configuration — resolves base URL + active account.
//
// Resolution order for the API token (highest first):
//   1. $SEDIMENT_TOKEN          — raw JWT in env (CI / headless one-shot)
//   2. $SEDIMENT_CREDENTIALS_FILE — JSON file with {token, account}
//   3. OS keychain entry for the active account
//
// Active account is determined by:
//   1. --account flag / $SEDIMENT_ACCOUNT env
//   2. ~/.config/sediment/default_account (set by `sediment auth default`)
//   3. The only account in the keychain, if exactly one exists
//   4. Error: "no account; run `sediment auth login`"

use anyhow::{anyhow, Context, Result};
use std::env;
use std::fs;
use std::path::PathBuf;

use crate::token;

const DEFAULT_PROD_URL: &str = "https://hypeproof-sediment.fly.dev";
const DEFAULT_DEV_URL: &str = "http://localhost:10100";

#[derive(Debug, Clone)]
pub struct Config {
    pub base_url: String,
    pub account: Option<String>, // None means "no account yet" (e.g. login)
    pub token: Option<String>,   // None until login or when env override missing
}

impl Config {
    pub fn resolve(account_flag: Option<String>, base_url_flag: Option<String>) -> Result<Self> {
        // Base URL: flag > env > dev/prod default
        let base_url = base_url_flag
            .or_else(|| env::var("SEDIMENT_BASE_URL").ok())
            .unwrap_or_else(|| {
                if env::var("SEDIMENT_DEV_MODE").as_deref() == Ok("1") {
                    DEFAULT_DEV_URL.into()
                } else {
                    DEFAULT_PROD_URL.into()
                }
            });

        // 1. Raw env token short-circuits everything.
        if let Ok(t) = env::var("SEDIMENT_TOKEN") {
            if !t.is_empty() {
                return Ok(Self {
                    base_url,
                    account: account_flag.or_else(|| env::var("SEDIMENT_ACCOUNT").ok()),
                    token: Some(t),
                });
            }
        }

        // 2. Credentials file env override.
        if let Ok(path) = env::var("SEDIMENT_CREDENTIALS_FILE") {
            let body = fs::read_to_string(&path).with_context(|| format!("reading {}", path))?;
            let parsed: CredsFile =
                serde_json::from_str(&body).with_context(|| format!("parsing {} as JSON", path))?;
            return Ok(Self {
                base_url,
                account: account_flag.or(Some(parsed.account.clone())),
                token: Some(parsed.token),
            });
        }

        // 3. Active account from flag or default file.
        let account = if let Some(a) = account_flag {
            Some(a)
        } else {
            read_default_account()?
        };

        // 4. Pull token from keychain if we have an account.
        let token = if let Some(ref acc) = account {
            token::get(acc).ok().flatten()
        } else {
            None
        };

        Ok(Self {
            base_url,
            account,
            token,
        })
    }

    pub fn require_token(&self) -> Result<&str> {
        self.token
            .as_deref()
            .filter(|t| !t.is_empty())
            .ok_or_else(|| anyhow!("not logged in — run `sediment auth login`"))
    }
}

#[derive(serde::Deserialize)]
struct CredsFile {
    token: String,
    account: String,
}

pub fn config_dir() -> Result<PathBuf> {
    // XDG_CONFIG_HOME wins; fallback to ~/.config (Linux/macOS) or
    // %APPDATA%\sediment (Windows) via dirs::config_dir.
    let base = if let Ok(x) = env::var("XDG_CONFIG_HOME") {
        PathBuf::from(x)
    } else {
        dirs::config_dir().ok_or_else(|| anyhow!("no config dir on this platform"))?
    };
    let dir = base.join("sediment");
    fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;
    Ok(dir)
}

pub fn default_account_file() -> Result<PathBuf> {
    Ok(config_dir()?.join("default_account"))
}

pub fn read_default_account() -> Result<Option<String>> {
    let path = default_account_file()?;
    if !path.exists() {
        // If exactly one account exists in the keychain, treat it as default.
        let accs = token::list_accounts().unwrap_or_default();
        if accs.len() == 1 {
            return Ok(Some(accs[0].clone()));
        }
        return Ok(None);
    }
    let s = fs::read_to_string(&path)?.trim().to_string();
    if s.is_empty() {
        Ok(None)
    } else {
        Ok(Some(s))
    }
}

pub fn write_default_account(account: &str) -> Result<()> {
    fs::write(default_account_file()?, account).context("writing default_account")
}
