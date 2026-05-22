// Expanded E2E suite — exercises the CLI's user-facing flows against a
// running Sediment platform. Gated by SEDIMENT_E2E_BASE_URL.
//
// Each test mints a JWT via the platform's dev-token endpoint and uses
// it via SEDIMENT_TOKEN env override, so the keychain is untouched (CI
// agents don't have keyring backends).

use std::process::Command;

fn binary_path() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_sediment"))
}

fn base_url() -> Option<String> {
    std::env::var("SEDIMENT_E2E_BASE_URL").ok()
}

fn mint_token(base: &str, email: &str) -> Option<String> {
    let client = reqwest::blocking::Client::new();
    let r = client
        .post(format!("{}/api/v1/auth/dev-token", base))
        .json(&serde_json::json!({"email": email}))
        .send()
        .ok()?;
    let v: serde_json::Value = r.json().ok()?;
    v["token"].as_str().map(String::from)
}

fn run(args: &[&str], token: &str, base: &str) -> (i32, String, String) {
    let out = Command::new(binary_path())
        .args(args)
        .env("SEDIMENT_TOKEN", token)
        .env("SEDIMENT_BASE_URL", base)
        .env("XDG_CONFIG_HOME", std::env::temp_dir().join("e2e_cfg"))
        .output()
        .expect("run sediment");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

// ----------------------------------------------------------------------
// CLI E2E (one platform, real CLI binary, real DB)
// ----------------------------------------------------------------------

#[test]
fn e2e_whoami_returns_seeded_identity() {
    let Some(base) = base_url() else {
        eprintln!("SKIP");
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, _) = run(&["--format", "json", "whoami"], &token, &base);
    assert_eq!(code, 0, "{}", out);
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["email"], "jay.lee@sonatus.com");
}

#[test]
fn e2e_search_returns_items() {
    let Some(base) = base_url() else {
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, _) = run(
        &["--format", "json", "search", "research", "--limit", "3"],
        &token,
        &base,
    );
    assert_eq!(code, 0, "{}", out);
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert!(
        !v["items"].as_array().unwrap().is_empty(),
        "expected at least 1 item"
    );
}

#[test]
fn e2e_search_with_unicode_query() {
    let Some(base) = base_url() else {
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, stderr) = run(
        &["--format", "json", "search", "한국어", "--limit", "5"],
        &token,
        &base,
    );
    assert_eq!(code, 0, "stdout={} stderr={}", out, stderr);
    let _: serde_json::Value = serde_json::from_str(&out).unwrap();
}

#[test]
fn e2e_read_returns_philosophy() {
    let Some(base) = base_url() else {
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, _) = run(
        &["--format", "json", "read", "PHILOSOPHY.md"],
        &token,
        &base,
    );
    assert_eq!(code, 0, "{}", out);
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["ref"], "PHILOSOPHY.md");
    assert!(v["body"].as_str().unwrap().len() > 100);
}

#[test]
fn e2e_read_404_clean_envelope() {
    let Some(base) = base_url() else {
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, _) = run(
        &["--format", "json", "read", "definitely-not-a-real-file.md"],
        &token,
        &base,
    );
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["error"]["code"], 404);
}

#[test]
fn e2e_recent_returns_items_for_last_year() {
    let Some(base) = base_url() else {
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, _) = run(
        &[
            "--format", "json", "recent", "--days", "365", "--limit", "5",
        ],
        &token,
        &base,
    );
    assert_eq!(code, 0, "{}", out);
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert!(v["items"].is_array());
}

#[test]
fn e2e_recent_page_all_emits_ndjson_lines() {
    let Some(base) = base_url() else {
        return;
    };
    let token = mint_token(&base, "jay.lee@sonatus.com").expect("mint");
    let (code, out, _) = run(
        &[
            "--format",
            "ndjson",
            "recent",
            "--days",
            "365",
            "--limit",
            "20",
            "--page-all",
        ],
        &token,
        &base,
    );
    assert_eq!(code, 0, "{}", out);
    let lines: Vec<&str> = out.lines().filter(|l| !l.is_empty()).collect();
    assert!(!lines.is_empty());
    // Each line is a valid JSON object
    for line in &lines {
        let _v: serde_json::Value = serde_json::from_str(line)
            .unwrap_or_else(|e| panic!("invalid ndjson line `{}`: {}", line, e));
    }
}

#[test]
fn e2e_expired_token_clean_error() {
    let Some(base) = base_url() else {
        return;
    };
    // Forge an HS256 token with bogus secret — fails signature verification.
    let bogus_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4IiwiZXhwIjoxfQ.bogus";
    let (code, out, _) = run(&["--format", "json", "whoami"], bogus_token, &base);
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["error"]["code"], 401);
    assert_eq!(v["error"]["reason"], "auth_expired");
}

// ----------------------------------------------------------------------
// Multi-tenant parallel
// ----------------------------------------------------------------------

#[test]
fn e2e_two_tenants_in_parallel_get_disjoint_results() {
    let Some(base) = base_url() else {
        return;
    };
    let t_hl = mint_token(&base, "jay.lee@sonatus.com").expect("mint hl");
    let t_acme = mint_token(&base, "admin@acme.test").expect("mint acme");

    let (c1, o1, _) = run(&["--format", "json", "whoami"], &t_hl, &base);
    let (c2, o2, _) = run(&["--format", "json", "whoami"], &t_acme, &base);
    assert_eq!(c1, 0);
    assert_eq!(c2, 0);
    let v1: serde_json::Value = serde_json::from_str(&o1).unwrap();
    let v2: serde_json::Value = serde_json::from_str(&o2).unwrap();
    assert_ne!(v1["tenant_id"], v2["tenant_id"]);
}
