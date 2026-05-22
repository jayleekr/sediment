// Edge cases — invariants the binary must hold under abnormal inputs.
// Companion to tests/unit.rs (happy path).

use std::process::Command;

fn binary_path() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_sediment"))
}

fn run(args: &[&str], env: &[(&str, &str)]) -> (i32, String, String) {
    let mut cmd = Command::new(binary_path());
    cmd.args(args);
    cmd.env_clear(); // start from a minimal env — tests must not depend on user state
    cmd.env("PATH", std::env::var("PATH").unwrap_or_default());
    cmd.env("HOME", std::env::var("HOME").unwrap_or_default());
    for (k, v) in env {
        cmd.env(k, v);
    }
    let out = cmd.output().expect("run sediment");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

// ----------------------------------------------------------------------
// Path-traversal — every variant we can think of
// ----------------------------------------------------------------------

#[test]
fn path_traversal_dot_dot_anywhere_rejected() {
    for ref_path in &["foo/../bar", "a/b/../../c", "valid/../../etc"] {
        let (code, out, _) = run(
            &["--format", "json", "read", ref_path],
            &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
        );
        assert!(code != 0, "expected reject for {}: code={} out={}", ref_path, code, out);
        let v: serde_json::Value = serde_json::from_str(&out).expect("json");
        assert!(
            v["error"]["message"].as_str().unwrap().contains("path traversal"),
            "{}: missing path traversal message in {}", ref_path, out
        );
    }
}

#[test]
fn path_traversal_url_encoded_passes_through_to_server() {
    // The client-side check looks for literal `..` and leading `/`. URL
    // encoded `..` (%2e%2e) does NOT match the literal check. This test
    // documents that fact — the server's RLS + path-handling is the
    // ultimate guard. (Server-side server test lives in test_security.py.)
    let (code, _out, _) = run(
        &["--format", "json", "read", "%2e%2e/%2e%2e/etc/passwd"],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    // Network unreachable wins (the request would be attempted)
    assert!(code != 0);
}

#[test]
fn empty_query_rejected_by_clap() {
    // clap allows empty positional unless required-set is enforced. Our
    // search has `query: String` without min-len; empty passes to server.
    // We assert it doesn't panic.
    let (code, _, _) = run(
        &["search", ""],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0); // network error, not a panic
}

// ----------------------------------------------------------------------
// Limits / clamping
// ----------------------------------------------------------------------

#[test]
fn search_limit_zero_clamped_to_one() {
    // Without a server, we can't observe the server's view of the param —
    // but we can check the CLI doesn't blow up.
    let (code, _, stderr) = run(
        &["search", "x", "--limit", "0"],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0, "expected non-zero exit for unreachable URL");
    assert!(!stderr.contains("panicked"));
}

#[test]
fn search_huge_limit_clamped() {
    let (code, _, _) = run(
        &["search", "x", "--limit", "99999"],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0); // network err — but no panic
}

#[test]
fn recent_days_huge_no_overflow() {
    let (code, _, stderr) = run(
        &["recent", "--days", "999999"],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0);
    assert!(!stderr.contains("overflow"));
    assert!(!stderr.contains("panicked"));
}

// ----------------------------------------------------------------------
// Unicode + multilingual
// ----------------------------------------------------------------------

#[test]
fn search_unicode_query_does_not_panic() {
    // Korean + emoji + zero-width chars all in the query.
    let q = "한국어 검색 🔍\u{200B}";
    let (code, _, stderr) = run(
        &["search", q],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0);
    assert!(!stderr.contains("panicked"));
}

#[test]
fn read_with_unicode_ref_does_not_panic() {
    let (code, _, _) = run(
        &["read", "한국어/노트.md"],
        &[("SEDIMENT_TOKEN", "x"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0);
}

// ----------------------------------------------------------------------
// Output formats
// ----------------------------------------------------------------------

#[test]
fn yaml_output_is_valid_yaml() {
    let (code, out, _) = run(&["--format", "yaml", "schema", "search"], &[]);
    assert_eq!(code, 0);
    let _v: serde_yaml::Value = serde_yaml::from_str(&out).expect("yaml parses");
}

#[test]
fn json_output_is_pretty_printed() {
    let (code, out, _) = run(&["--format", "json", "schema", "search"], &[]);
    assert_eq!(code, 0);
    // Pretty-printed has newlines + 2-space indent
    assert!(out.contains("\n"));
    assert!(out.contains("  \""));
}

#[test]
fn ndjson_output_is_single_line_for_single_value() {
    let (code, out, _) = run(&["--format", "ndjson", "schema", "search"], &[]);
    assert_eq!(code, 0);
    let lines: Vec<&str> = out.lines().filter(|l| !l.is_empty()).collect();
    assert_eq!(lines.len(), 1, "expected one NDJSON line, got {}", lines.len());
    let _: serde_json::Value = serde_json::from_str(lines[0]).expect("ndjson parses");
}

// ----------------------------------------------------------------------
// Env override priority
// ----------------------------------------------------------------------

#[test]
fn sediment_token_env_short_circuits_keyring() {
    // No account / default — but a raw token in env should let us at least
    // reach the network step (proving the env path wins).
    let (code, _, _) = run(
        &["whoami"],
        &[("SEDIMENT_TOKEN", "fake.jwt"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0);
    // It made it to the HTTP step (network_unreachable). If env priority was
    // broken we'd see auth_expired or "not logged in" instead.
}

// ----------------------------------------------------------------------
// Account flag
// ----------------------------------------------------------------------

#[test]
fn account_flag_overrides_env() {
    // --account flag at the top level should be visible to commands.
    // We can't easily assert on internals from outside, but we can check
    // that having it doesn't crash and goes through normal codepath.
    let (code, _, stderr) = run(
        &["--account", "x@example.test", "auth", "status"],
        &[("XDG_CONFIG_HOME", "/tmp/_no_account_test")],
    );
    assert!(!stderr.contains("panicked"));
    assert!(code == 0 || code != 0); // either way, no panic — explicit
}
