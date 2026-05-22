// Integration-style tests that exercise the public binary's exit codes and
// stdout envelope shape. These are the load-bearing UTs per the design doc.
//
// Note: many internal modules (output, error, etc.) aren't easily reachable
// from an external integration test because `cargo new --bin` doesn't expose
// them as a library. We use the binary path under target/debug + assert on
// its observable behavior. CI keeps these tests fast by mocking the network
// via wiremock for everything that hits HTTP.

use std::process::Command;

fn binary_path() -> std::path::PathBuf {
    // Cargo injects this for integration tests under tests/.
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_sediment"))
}

fn run(args: &[&str], env: &[(&str, &str)]) -> (i32, String, String) {
    let mut cmd = Command::new(binary_path());
    cmd.args(args);
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

#[test]
fn help_lists_all_subcommands() {
    let (code, out, _) = run(&["--help"], &[]);
    assert_eq!(code, 0);
    for cmd in &[
        "auth", "whoami", "search", "ask", "read", "recent", "schema",
    ] {
        assert!(out.contains(cmd), "expected help to mention {}: {}", cmd, out);
    }
}

#[test]
fn version_flag_works() {
    let (code, out, _) = run(&["--version"], &[]);
    assert_eq!(code, 0);
    assert!(out.contains("sediment"));
}

#[test]
fn no_token_produces_auth_envelope_with_hint() {
    // No SEDIMENT_TOKEN, no keychain. whoami should fail with code != 0 and
    // emit a JSON envelope on stdout.
    let (code, out, _) = run(
        &["--account", "nobody@example.test", "whoami"],
        // empty creds dir to ensure keychain is empty
        &[("SEDIMENT_DEV_MODE", "1"), ("XDG_CONFIG_HOME", "/tmp/_sediment_t_no_token")],
    );
    assert!(code != 0, "expected non-zero exit for unauthenticated call");
    let parsed: serde_json::Value =
        serde_json::from_str(&out).expect(&format!("stdout was not JSON: {}", out));
    assert!(parsed.get("error").is_some(), "missing error envelope: {}", out);
}

#[test]
fn schema_command_returns_static_json() {
    let (code, out, _) = run(&["--format", "json", "schema", "search"], &[]);
    assert_eq!(code, 0, "schema exit was non-zero: {}", out);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert_eq!(v["name"], "search");
    assert!(v["params"]["limit"]["maximum"] == 50);
}

#[test]
fn schema_unknown_tool_errors() {
    let (code, out, _) = run(&["schema", "nonsense"], &[]);
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert_eq!(v["error"]["reason"], "internal_error");
    assert!(v["error"]["message"].as_str().unwrap().contains("nonsense"));
}

#[test]
fn read_rejects_path_traversal_client_side() {
    // Even without a token, the path-traversal check should fire first because
    // it's evaluated before require_token in commands::read. Actually it's
    // not — require_token runs first via the call sequence. Validate with a
    // token set so the check fires.
    let (code, out, _) = run(
        &["--format", "json", "read", "../../../etc/passwd"],
        &[
            ("SEDIMENT_TOKEN", "any.jwt.string"),
            ("SEDIMENT_BASE_URL", "http://127.0.0.1:1"), // bogus, won't be hit
        ],
    );
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert!(
        v["error"]["message"]
            .as_str()
            .unwrap()
            .contains("path traversal"),
        "expected path traversal message: {}",
        out
    );
}

#[test]
fn read_rejects_absolute_path_client_side() {
    let (code, out, _) = run(
        &["--format", "json", "read", "/etc/passwd"],
        &[("SEDIMENT_TOKEN", "any.jwt"), ("SEDIMENT_BASE_URL", "http://127.0.0.1:1")],
    );
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert!(v["error"]["message"]
        .as_str()
        .unwrap()
        .contains("path traversal"));
}

#[test]
fn network_error_emits_network_unreachable_envelope() {
    // Point at a port nothing is listening on; expect a clean envelope, no
    // panic stack trace.
    let (code, out, _) = run(
        &["--format", "json", "whoami"],
        &[
            ("SEDIMENT_TOKEN", "fake.jwt.token"),
            ("SEDIMENT_BASE_URL", "http://127.0.0.1:1"),
        ],
    );
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    let reason = v["error"]["reason"].as_str().unwrap_or("");
    // Either network_unreachable (connection refused) or internal_error
    // (request build failure on some platforms) — both must NOT be a panic.
    assert!(
        ["network_unreachable", "internal_error"].contains(&reason),
        "unexpected reason {} in {}",
        reason,
        out
    );
}

#[test]
fn invalid_token_against_unreachable_base_doesnt_panic() {
    let (code, _out, stderr) = run(
        &["whoami"],
        &[
            ("SEDIMENT_TOKEN", "not-a-real-jwt"),
            ("SEDIMENT_BASE_URL", "http://127.0.0.1:1"),
        ],
    );
    assert!(code != 0);
    // The error envelope goes to stdout; stderr must be silent (no rust
    // backtrace leaking).
    assert!(
        !stderr.contains("panicked"),
        "binary panicked: stderr={}",
        stderr
    );
}

#[test]
fn search_query_required() {
    let (code, _out, stderr) = run(&["search"], &[]);
    assert!(code != 0);
    // clap emits its own usage error to stderr — that's the expected channel.
    assert!(
        stderr.contains("required") || stderr.contains("Usage"),
        "expected clap usage error, got {}",
        stderr
    );
}
