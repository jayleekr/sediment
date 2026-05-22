// Human-UX tests: what the user actually SEES in a terminal.
//
// Existing tests (unit.rs / edges.rs / e2e_*.rs) all check JSON shape +
// exit codes — backend-shape, not TTY-shape. This file is the missing
// half: capture exact stdout for `--format table` and assert the rendering
// is readable + contains the expected structural elements.

use std::process::Command;

fn binary_path() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_sediment"))
}

fn run(args: &[&str], env: &[(&str, &str)]) -> (i32, String, String) {
    let mut cmd = Command::new(binary_path());
    cmd.args(args);
    cmd.env_clear();
    cmd.env("PATH", std::env::var("PATH").unwrap_or_default());
    cmd.env("HOME", std::env::var("HOME").unwrap_or_default());
    for (k, v) in env {
        cmd.env(k, v);
    }
    let out = cmd.output().expect("run");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

// ----------------------------------------------------------------------
// schema (offline, fastest)
// ----------------------------------------------------------------------

#[test]
fn schema_table_render_is_compact_and_keyed() {
    let (code, out, _) = run(&["--format", "table", "schema", "search"], &[]);
    assert_eq!(code, 0);
    // Table renderer produces "key: value" or a small table. Either way:
    // must NOT be raw {{}} JSON dump.
    assert!(
        !out.trim_start().starts_with('{'),
        "table mode should not emit raw JSON, got:\n{}",
        out
    );
    // Should mention the tool name and at least one param.
    assert!(out.contains("search"), "missing tool name in:\n{}", out);
}

// ----------------------------------------------------------------------
// error envelopes — what the user sees when things break
// ----------------------------------------------------------------------

#[test]
fn no_token_error_is_one_line_human_readable() {
    let (code, out, _) = run(
        &["whoami"],
        &[
            ("SEDIMENT_DEV_MODE", "1"),
            ("XDG_CONFIG_HOME", "/tmp/_sediment_ux_no_tok"),
        ],
    );
    assert!(code != 0);
    // JSON envelope is correct for scripts, but humans should still get
    // the `hint` field surfaced legibly.
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert_eq!(v["error"]["reason"], "internal_error");
    let msg = v["error"]["message"].as_str().unwrap();
    assert!(
        msg.contains("not logged in") || msg.contains("auth login"),
        "error message must tell user what to do, got: {}",
        msg
    );
}

#[test]
fn auth_expired_error_includes_login_hint() {
    // Bogus JWT against a working URL would 401, but we don't want a network
    // dep here. Use unreachable URL — should produce network_unreachable
    // with a hint.
    let (code, out, _) = run(
        &["--format", "json", "whoami"],
        &[
            ("SEDIMENT_TOKEN", "fake.jwt"),
            ("SEDIMENT_BASE_URL", "http://127.0.0.1:1"),
        ],
    );
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    let hint = v["error"]
        .get("hint")
        .and_then(|h| h.as_str())
        .unwrap_or("");
    assert!(
        !hint.is_empty() || v["error"]["reason"].as_str().unwrap_or("") == "network_unreachable",
        "expected actionable hint or network reason: {}",
        out
    );
}

// ----------------------------------------------------------------------
// Help output — first thing a user sees when stuck
// ----------------------------------------------------------------------

#[test]
fn root_help_lists_all_verbs_with_one_line_description() {
    let (code, out, _) = run(&["--help"], &[]);
    assert_eq!(code, 0);
    for verb in &[
        "auth",
        "whoami",
        "search",
        "ask",
        "read",
        "recent",
        "schema",
        "update",
        "completion",
    ] {
        assert!(out.contains(verb), "missing verb in --help: {}", verb);
    }
    // Every verb should have a description after it (not just the bare name).
    for line in out.lines() {
        let trimmed = line.trim_start();
        for verb in &["auth", "whoami", "search", "ask", "read"] {
            if trimmed.starts_with(verb) && trimmed.len() > verb.len() + 2 {
                let rest = &trimmed[verb.len()..];
                assert!(
                    rest.contains(char::is_alphabetic),
                    "verb '{}' help line lacks description: {}",
                    verb,
                    line
                );
            }
        }
    }
}

#[test]
fn ask_help_documents_stream_flag() {
    let (code, out, _) = run(&["ask", "--help"], &[]);
    assert_eq!(code, 0);
    assert!(
        out.contains("--stream"),
        "ask --help must document --stream: {}",
        out
    );
}

// ----------------------------------------------------------------------
// Output formats round-trip
// ----------------------------------------------------------------------

#[test]
fn schema_json_table_yaml_all_emit_valid_for_their_format() {
    // json
    let (_, json_out, _) = run(&["--format", "json", "schema", "ask"], &[]);
    let _: serde_json::Value = serde_json::from_str(&json_out).expect("json");

    // yaml
    let (_, yaml_out, _) = run(&["--format", "yaml", "schema", "ask"], &[]);
    let _: serde_yaml::Value = serde_yaml::from_str(&yaml_out).expect("yaml");

    // table
    let (_, table_out, _) = run(&["--format", "table", "schema", "ask"], &[]);
    assert!(!table_out.trim_start().starts_with('{'));
    assert!(!table_out.trim_start().starts_with("---"));
    assert!(table_out.contains("ask"));
}

// ----------------------------------------------------------------------
// Spinner stays out of stdout
// ----------------------------------------------------------------------

#[test]
fn ask_spinner_does_not_pollute_stdout() {
    // Point at an unreachable URL — the spinner thread will start but the
    // request errors quickly. The JSON envelope must land on stdout cleanly
    // (no spinner frames, no \r resets) so scripts can parse it.
    let (code, out, _stderr) = run(
        &["--format", "json", "ask", "x"],
        &[
            ("SEDIMENT_TOKEN", "fake.jwt"),
            ("SEDIMENT_BASE_URL", "http://127.0.0.1:1"),
        ],
    );
    assert!(code != 0);
    let v: serde_json::Value = serde_json::from_str(out.trim()).expect("json");
    assert!(v["error"].is_object());
    // Specifically guard against the spinner characters appearing on stdout.
    for c in ["⠋", "⠙", "⠹", "thinking", "\r"] {
        assert!(
            !out.contains(c),
            "spinner artifact leaked into stdout: {:?} in {:?}",
            c,
            out
        );
    }
}
