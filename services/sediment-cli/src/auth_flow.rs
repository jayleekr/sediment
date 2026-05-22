// RFC 8628 Device Authorization Grant — client side.
//
// CLI flow:
//   1. POST /api/v1/auth/oauth-device/start
//   2. Print verification_uri + user_code to stderr (so stdout stays
//      machine-parseable if someone pipes us).
//   3. Try to open the URI in the user's default browser (best-effort).
//   4. POST /api/v1/auth/oauth-device/poll every `interval` seconds.
//      Honor `slow_down` by doubling interval.
//      Stop on 200 (token), on `expired_token`, on `access_denied`, or
//      after the start's `expires_in` budget elapses.

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use tokio::time::sleep;

use crate::http::Http;

#[derive(Debug, Serialize)]
pub struct StartReq {
    pub client_id: String,
    pub scope: String,
}

#[derive(Debug, Deserialize)]
pub struct StartResp {
    pub device_code: String,
    pub user_code: String,
    #[allow(dead_code)]
    pub verification_uri: String,
    pub verification_uri_complete: String,
    pub expires_in: u64,
    pub interval: u64,
}

#[derive(Debug, Deserialize)]
pub struct TokenResp {
    pub token: String,
    pub member_id: String,
    pub tenant_id: String,
    pub role: String,
    pub display_name: String,
}

pub async fn run_device_flow(http: &Http) -> Result<TokenResp> {
    let start: StartResp = http
        .post_json(
            "/api/v1/auth/oauth-device/start",
            &StartReq {
                client_id: "sediment-cli".into(),
                scope: "default".into(),
            },
        )
        .await?;

    eprintln!();
    eprintln!("To finish login, open this URL in any browser:");
    eprintln!("    {}", start.verification_uri_complete);
    eprintln!();
    eprintln!("And enter the code (if prompted): {}", start.user_code);
    eprintln!();
    let _ = try_open_browser(&start.verification_uri_complete);

    // Poll until approved, denied, or budget elapsed.
    let deadline = Instant::now() + Duration::from_secs(start.expires_in);
    let mut interval = Duration::from_secs(start.interval);
    let max_interval = Duration::from_secs(30);

    while Instant::now() < deadline {
        sleep(interval).await;

        let resp = http
            .raw_post(
                "/api/v1/auth/oauth-device/poll",
                serde_json::json!({"device_code": start.device_code}),
            )
            .await;

        match resp {
            Ok(r) => {
                // 200 path → parse the token response.
                let token: TokenResp = r.json().await?;
                return Ok(token);
            }
            Err(e) => {
                // ApiError envelope carries the 400 body which contains
                // the RFC 8628 error code ({"detail":{"error":"..."}})
                let s = format!("{}", e);
                if s.contains("authorization_pending") {
                    continue;
                } else if s.contains("slow_down") {
                    interval = (interval + Duration::from_secs(5)).min(max_interval);
                    continue;
                } else if s.contains("access_denied") {
                    return Err(anyhow!("device login denied"));
                } else if s.contains("expired_token") {
                    return Err(anyhow!(
                        "device code expired — re-run `sediment auth login`"
                    ));
                } else {
                    return Err(e);
                }
            }
        }
    }
    Err(anyhow!("device login timed out"))
}

fn try_open_browser(url: &str) -> Result<()> {
    // Best-effort — don't fail the flow if the platform open command isn't
    // available (CI, headless box, container).
    #[cfg(target_os = "macos")]
    let cmd = "open";
    #[cfg(target_os = "linux")]
    let cmd = "xdg-open";
    #[cfg(target_os = "windows")]
    let cmd = "cmd";

    #[cfg(any(target_os = "macos", target_os = "linux"))]
    let _ = std::process::Command::new(cmd).arg(url).spawn();
    #[cfg(target_os = "windows")]
    let _ = std::process::Command::new(cmd)
        .args(["/C", "start", url])
        .spawn();

    Ok(())
}
