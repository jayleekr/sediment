// End-to-end test for the device-flow login against a running platform.
//
// Gated by SEDIMENT_E2E_BASE_URL — when unset, the test is skipped so CI
// doesn't have to spin up the stack just to compile the Rust crate. To run:
//
//   make up                                       # postgres + redis
//   cd services/sediment && uvicorn ... --port 10101 &     # platform
//   SEDIMENT_E2E_BASE_URL=http://localhost:10101 \
//   SEDIMENT_E2E_EMAIL=jay.lee@sonatus.com \
//     cargo test --test e2e_login -- --nocapture
//
// What it exercises:
//   1. Spawn `sediment auth login` with --base-url pointing at the running
//      stack + a temporary XDG_CONFIG_HOME (so we don't pollute the user's
//      real keychain — but on macOS the keychain isn't XDG, so we use the
//      env-token path to avoid that side effect entirely).
//   2. Drive the device flow as the "user": read the user_code from stderr,
//      hit /oauth-device/approve-dev with the seed email.
//   3. Wait for the CLI to finish; assert exit 0 + JSON envelope on stdout.

use std::env;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

fn binary_path() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_sediment"))
}

#[test]
fn e2e_device_login_against_running_platform() {
    let base = match env::var("SEDIMENT_E2E_BASE_URL") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("SKIP: set SEDIMENT_E2E_BASE_URL to run");
            return;
        }
    };
    let email = env::var("SEDIMENT_E2E_EMAIL").unwrap_or_else(|_| "jay.lee@sonatus.com".into());

    // Tempdir to isolate XDG_CONFIG_HOME — accounts index lives here, but
    // the keychain is OS-native, so this is only a partial isolation. The
    // test still mutates the keychain entry for the bound email.
    let tmp = tempfile::tempdir().expect("tempdir");

    let mut child = Command::new(binary_path())
        .arg("--format")
        .arg("json")
        .arg("--base-url")
        .arg(&base)
        .arg("auth")
        .arg("login")
        .env("XDG_CONFIG_HOME", tmp.path())
        .env("SEDIMENT_DEV_MODE", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sediment");

    // Drain stderr in a thread to capture the user_code.
    let stderr = child.stderr.take().unwrap();
    let (tx, rx) = mpsc::channel::<String>();
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut buf = String::new();
        loop {
            buf.clear();
            match reader.read_line(&mut buf) {
                Ok(0) => break,
                Ok(_) => {
                    let _ = tx.send(buf.clone());
                }
                Err(_) => break,
            }
        }
    });

    // Wait for the user_code to appear (CLI prints it on stderr).
    let user_code = {
        let mut found: Option<String> = None;
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while std::time::Instant::now() < deadline && found.is_none() {
            match rx.recv_timeout(Duration::from_secs(1)) {
                Ok(line) => {
                    if let Some(code) = extract_user_code(&line) {
                        found = Some(code);
                    }
                }
                Err(_) => continue,
            }
        }
        found.expect("did not see user_code on stderr in time")
    };
    eprintln!("test saw user_code = {}", user_code);

    // Wait one poll interval before approving so the first poll returns
    // authorization_pending (exercises the polling loop).
    thread::sleep(Duration::from_secs(2));

    // Approve via dev shortcut.
    let client = reqwest::blocking::Client::new();
    let resp = client
        .post(format!("{}/api/v1/auth/oauth-device/approve-dev", base))
        .json(&serde_json::json!({"user_code": user_code, "email": email}))
        .send()
        .expect("approve-dev POST");
    assert!(
        resp.status().is_success(),
        "approve-dev failed: {} {}",
        resp.status(),
        resp.text().unwrap_or_default()
    );

    // Wait for CLI to complete (max 60s — interval is 5s; first poll already
    // happened, second poll will succeed).
    let out = child
        .wait_with_output()
        .expect("CLI wait_with_output");
    assert!(
        out.status.success(),
        "CLI exited non-zero: stdout={}, stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr),
    );

    let stdout = String::from_utf8_lossy(&out.stdout);
    let v: serde_json::Value = serde_json::from_str(&stdout)
        .unwrap_or_else(|e| panic!("login stdout not JSON: {} ({})", stdout, e));
    assert_eq!(v["ok"], true);
    assert_eq!(v["account"].as_str().unwrap().to_lowercase(), email.to_lowercase());
    assert!(v["member_id"].is_string());
    assert!(v["tenant_id"].is_string());
}

fn extract_user_code(line: &str) -> Option<String> {
    // Looking for "And enter the code (if prompted): XXXX-YYYY"
    let needle = "code (if prompted):";
    let i = line.find(needle)?;
    let rest = line[i + needle.len()..].trim();
    // user code is 4-4 uppercase chars
    let first = rest.split_whitespace().next()?;
    if first.len() == 9 && first.as_bytes()[4] == b'-' {
        Some(first.to_string())
    } else {
        None
    }
}
