// All subcommand implementations.
//
// Each public fn is one CLI verb. The pattern is identical:
//   1. Validate inputs (clamps, path-traversal check).
//   2. Build the Http client from config (token may be None for `auth login`).
//   3. Call the endpoint.
//   4. Render via output::render.

use anyhow::{anyhow, Result};
use serde_json::{json, Value};

use crate::auth_flow;
use crate::config::{self, Config};
use crate::http::Http;
use crate::output::{self, Format};
use crate::token;

// ============================================================
// auth
// ============================================================

pub async fn auth_login(cfg: &Config, account_hint: Option<String>, fmt: Format) -> Result<()> {
    let http = Http::new(cfg.base_url.clone(), None)?;
    let token = auth_flow::run_device_flow(&http).await?;

    // Bind to the email reported by the server (single source of truth) and
    // honor any user-provided hint only when it matches.
    let bound_email = lookup_email(&http, &token.token).await?;
    if let Some(hint) = account_hint {
        if hint.to_lowercase() != bound_email.to_lowercase() {
            return Err(anyhow!(
                "--account {} but the device flow bound {} — re-run without --account",
                hint,
                bound_email
            ));
        }
    }

    token::set(&bound_email, &token.token)?;

    // If this is the first/only account, also set it as the default for
    // future commands invoked without --account.
    if config::read_default_account()?.is_none() {
        config::write_default_account(&bound_email)?;
    }

    let result = json!({
        "ok": true,
        "account": bound_email,
        "member_id": token.member_id,
        "tenant_id": token.tenant_id,
        "role": token.role,
        "display_name": token.display_name,
        "hint": "credentials stored in OS keychain",
    });
    output::render(&result, fmt)
}

pub async fn auth_status(cfg: &Config, fmt: Format) -> Result<()> {
    let acc = cfg
        .account
        .clone()
        .ok_or_else(|| anyhow!("no active account — run `sediment auth login`"))?;
    let tok = cfg.token.as_deref().filter(|t| !t.is_empty());
    let Some(tok) = tok else {
        output::render(&json!({"account": acc, "logged_in": false}), fmt)?;
        return Ok(());
    };
    let http = Http::new(cfg.base_url.clone(), Some(tok.to_string()))?;
    match http.get_json::<Value>("/api/v1/auth/whoami").await {
        Ok(w) => {
            let body = json!({
                "account": acc,
                "logged_in": true,
                "base_url": cfg.base_url,
                "whoami": w,
            });
            output::render(&body, fmt)
        }
        Err(e) => {
            let body = json!({
                "account": acc,
                "logged_in": false,
                "base_url": cfg.base_url,
                "error": format!("{}", e),
            });
            output::render(&body, fmt)
        }
    }
}

pub async fn auth_logout(cfg: &Config, all: bool, fmt: Format) -> Result<()> {
    if all {
        let accs = token::list_accounts()?;
        for a in &accs {
            token::delete(a)?;
        }
        // Wipe default-account pointer.
        let _ = std::fs::remove_file(config::default_account_file()?);
        return output::render(&json!({"ok": true, "removed": accs}), fmt);
    }
    let acc = cfg
        .account
        .clone()
        .ok_or_else(|| anyhow!("no active account — pass --account or --all"))?;
    token::delete(&acc)?;
    // If we just removed the default, clear that pointer.
    if config::read_default_account()?.as_deref() == Some(acc.as_str()) {
        let _ = std::fs::remove_file(config::default_account_file()?);
    }
    output::render(&json!({"ok": true, "removed": acc}), fmt)
}

pub async fn auth_list(fmt: Format) -> Result<()> {
    let accs = token::list_accounts()?;
    let default = config::read_default_account()?;
    let items: Vec<Value> = accs
        .iter()
        .map(|a| json!({"account": a, "is_default": Some(a.clone()) == default}))
        .collect();
    output::render(&json!({"items": items}), fmt)
}

pub async fn auth_set_default(account: &str, fmt: Format) -> Result<()> {
    let accs = token::list_accounts()?;
    if !accs.iter().any(|a| a == account) {
        return Err(anyhow!(
            "account `{}` has no stored credentials — run `sediment auth login --account {}`",
            account,
            account
        ));
    }
    config::write_default_account(account)?;
    output::render(&json!({"ok": true, "default_account": account}), fmt)
}

async fn lookup_email(http: &Http, token: &str) -> Result<String> {
    // Hit /whoami with the just-minted token; use the bound email.
    let h = Http::new(http.base_url.clone(), Some(token.to_string()))?;
    let v: Value = h.get_json("/api/v1/auth/whoami").await?;
    v.get("email")
        .and_then(|e| e.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .ok_or_else(|| anyhow!("whoami did not return an email"))
}

// ============================================================
// whoami / search / read / recent / ask / schema
// ============================================================

pub async fn whoami(cfg: &Config, fmt: Format) -> Result<()> {
    let tok = cfg.require_token()?;
    let http = Http::new(cfg.base_url.clone(), Some(tok.into()))?;
    let v: Value = http.get_json("/api/v1/auth/whoami").await?;
    output::render(&v, fmt)
}

pub async fn search(
    cfg: &Config,
    query: &str,
    limit: u32,
    r#type: Option<&str>,
    fmt: Format,
) -> Result<()> {
    let limit = limit.clamp(1, 50);
    let tok = cfg.require_token()?;
    let http = Http::new(cfg.base_url.clone(), Some(tok.into()))?;
    let mut q: Vec<(&str, String)> = vec![("q", query.into()), ("limit", limit.to_string())];
    if let Some(t) = r#type {
        q.push(("type", t.into()));
    }
    let v: Value = http.get_json_with("/api/v1/library/search", &q).await?;
    output::render(&v, fmt)
}

pub async fn read(cfg: &Config, r#ref: &str, fmt: Format) -> Result<()> {
    // Client-side path-traversal guard before hitting the server.
    if r#ref.contains("..") || r#ref.starts_with('/') {
        return Err(anyhow!("invalid ref (path traversal not allowed)"));
    }
    let tok = cfg.require_token()?;
    let http = Http::new(cfg.base_url.clone(), Some(tok.into()))?;
    // Pass ref as path component — server handles URL-decoding.
    let path = format!("/api/v1/library/{}", r#ref);
    let v: Value = http.get_json(&path).await?;
    output::render(&v, fmt)
}

pub async fn recent(
    cfg: &Config,
    days: u32,
    r#type: Option<&str>,
    limit: u32,
    page_all: bool,
    fmt: Format,
) -> Result<()> {
    let days = days.clamp(1, 365);
    let limit = limit.clamp(1, 100);
    let tok = cfg.require_token()?;
    let http = Http::new(cfg.base_url.clone(), Some(tok.into()))?;
    // Server treats `date_from` as ISO; compute on client to avoid timezone
    // surprises. Use a simple Unix-epoch math via chrono-less approach.
    let date_from = epoch_days_ago(days);
    let mut q: Vec<(&str, String)> = vec![
        ("date_from", date_from.clone()),
        ("limit", limit.to_string()),
    ];
    if let Some(t) = r#type {
        q.push(("type", t.into()));
    }

    if !page_all {
        let v: Value = http.get_json_with("/api/v1/library", &q).await?;
        return output::render(&v, fmt);
    }

    // --page-all: fetch successive pages until empty; emit one JSON line per
    // page on stdout regardless of --format (NDJSON semantics).
    // Hard cap: 50 pages. If the server doesn't honor offset, we'd loop
    // forever otherwise — so we also bail if a page's first ref repeats.
    let mut offset = 0u32;
    let mut last_first_ref: Option<String> = None;
    const MAX_PAGES: u32 = 50;
    for page in 0..MAX_PAGES {
        let mut paged = q.clone();
        paged.push(("offset", offset.to_string()));
        let v: Value = http.get_json_with("/api/v1/library", &paged).await?;
        let items_arr = v.get("items").and_then(|i| i.as_array());
        let items_len = items_arr.map(|a| a.len() as u32).unwrap_or(0);
        // Repeat-detection: if the first ref equals the previous page's first
        // ref, the server isn't paginating. Bail to avoid an infinite loop.
        let first_ref = items_arr
            .and_then(|a| a.first())
            .and_then(|f| f.get("ref"))
            .and_then(|r| r.as_str())
            .map(String::from);
        if page > 0 && first_ref.is_some() && first_ref == last_first_ref {
            eprintln!(
                "(--page-all: server did not honor offset; stopping after {} pages)",
                page
            );
            break;
        }
        last_first_ref = first_ref;
        output::render_ndjson_line(&v)?;
        if items_len < limit {
            break;
        }
        offset += items_len;
    }
    Ok(())
}

fn epoch_days_ago(days: u32) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        .saturating_sub((days as u64) * 86400);
    // Format YYYY-MM-DD via libc gmtime — avoid pulling chrono in.
    let days_since_epoch = (secs / 86400) as i64;
    let (y, m, d) = civil_from_days(days_since_epoch);
    format!("{:04}-{:02}-{:02}", y, m, d)
}

// Howard Hinnant's date algorithm — small and dependency-free.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

pub async fn ask(cfg: &Config, query: &str, stream: bool, fmt: Format) -> Result<()> {
    use futures_util::StreamExt;

    let tok = cfg.require_token()?;
    // Use platform base for /api/v1/conversations + langgraph base for stream.
    let http = Http::new(cfg.base_url.clone(), Some(tok.into()))?;

    // 1. Create a transient conversation.
    let conv: Value = http
        .post_json(
            "/api/v1/conversations",
            &json!({"title": query.chars().take(60).collect::<String>()}),
        )
        .await?;
    let conv_id = conv
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow!("conversations API did not return id"))?
        .to_string();

    // 2. Open the SSE stream against the langgraph base — for now, derive it
    // from the platform base by swapping :10100 → :10020 (local dev). When
    // the API gateway fronts both behind one URL (production via nginx), the
    // same base works.
    let lg_base = derive_langgraph_base(&cfg.base_url);
    let lg_http = Http::new(lg_base, Some(tok.into()))?;
    let resp = lg_http
        .raw_post(
            "/v1/sediment/stream",
            json!({"conv_id": conv_id, "query": query}),
        )
        .await?;

    let mut answer = String::new();
    let mut citations: Vec<Value> = Vec::new();
    let mut byte_buf = String::new();
    let mut cur_event = "message".to_string();
    let mut stream_iter = resp.bytes_stream();

    while let Some(chunk) = stream_iter.next().await {
        let bytes = chunk?;
        byte_buf.push_str(&String::from_utf8_lossy(&bytes));
        // SSE: frames separated by "\n\n"
        while let Some(idx) = byte_buf.find("\n\n") {
            let frame: String = byte_buf.drain(..idx + 2).collect();
            let mut data_lines: Vec<&str> = Vec::new();
            for line in frame.lines() {
                if let Some(name) = line.strip_prefix("event: ") {
                    cur_event = name.trim().to_string();
                } else if let Some(payload) = line.strip_prefix("data: ") {
                    data_lines.push(payload);
                }
            }
            let data = data_lines.join("\n");
            if data == "[DONE]" {
                continue;
            }
            let Ok(value) = serde_json::from_str::<Value>(&data) else {
                continue;
            };
            match cur_event.as_str() {
                "delta" => {
                    if let Some(v) = value.get("v").and_then(|v| v.as_str()) {
                        answer.push_str(v);
                        if stream {
                            use std::io::Write;
                            let stdout = std::io::stdout();
                            let mut o = stdout.lock();
                            let _ = o.write_all(v.as_bytes());
                            let _ = o.flush();
                        }
                    }
                }
                "citation" => {
                    if let Some(c) = value.get("v") {
                        citations.push(c.clone());
                    }
                }
                _ => {}
            }
        }
    }

    if stream {
        println!(); // close the streamed answer with a newline
    }
    let warning = if answer.starts_with("[offline LLM mock]") {
        Some(
            "service is running with offline LLM mock — citations are real, answer text is stubbed",
        )
    } else {
        None
    };
    let mut result = json!({
        "answer": answer.trim(),
        "citations": citations,
        "conv_id": conv_id,
    });
    if let Some(w) = warning {
        result["warning"] = json!(w);
    }
    if !stream {
        output::render(&result, fmt)?;
    } else if !citations.is_empty() {
        // After the streamed answer, emit citations as a final NDJSON line so
        // the user (or LLM) can pick them up.
        println!();
        output::render_ndjson_line(&json!({"citations": citations}))?;
    }
    Ok(())
}

fn derive_langgraph_base(platform_base: &str) -> String {
    // localhost: :10100 → :10020. Hosted: same origin (nginx routes /v1/* to
    // langgraph internally).
    if platform_base.contains(":10100") {
        platform_base.replace(":10100", ":10020")
    } else {
        platform_base.to_string()
    }
}

pub async fn schema(cfg: &Config, tool: &str, fmt: Format) -> Result<()> {
    // Schemas are static — derived from the CLI's own knowledge of the API
    // surface. This matches the shape MCP shim expects so the shim can
    // forward `sediment schema X` byte-identically to its own tool descriptor.
    let _ = cfg; // not used; kept for future server-side schema fetch
    let v = match tool {
        "search" => json!({
            "name": "search",
            "description": "Hybrid search over the team's vault. Returns ranked chunks with refs.",
            "params": {
                "query": {"type": "string", "required": true},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                "type":  {"type": "string", "enum": ["column","research","novel","note","decision","meeting"]}
            }
        }),
        "ask" => json!({
            "name": "ask",
            "description": "Natural-language question. Streams the answer + citations.",
            "params": { "query": {"type": "string", "required": true} }
        }),
        "read" => json!({
            "name": "read",
            "description": "Fetch one artifact body by ref path.",
            "params": { "ref": {"type": "string", "required": true} }
        }),
        "recent" => json!({
            "name": "recent",
            "description": "List artifacts added/updated in the last N days.",
            "params": {
                "days":  {"type": "integer", "minimum": 1, "maximum": 365, "default": 7},
                "type":  {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
            }
        }),
        "whoami" => json!({
            "name": "whoami",
            "description": "Return the authenticated identity (member + tenant).",
            "params": {}
        }),
        other => return Err(anyhow!("unknown tool: {}", other)),
    };
    output::render(&v, fmt)
}
