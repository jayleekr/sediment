# CLI Multi-User Access — Design

> Status: **Decision locked, not yet implemented**
> Owner: Jay
> Last updated: 2026-05-22

## Goal

Let any HypeProof Lab teammate call Sediment from their own Claude Code
session — without cloning the repo, without a Python venv, and without
manually pasting JWTs. Multi-tenant by construction, efficient under
concurrent use, testable at UT/IT/E2E/stress tiers before code lands.

## Problem with today

`/sediment-connect` writes a Claude Code MCP entry that points at
`services/sediment/.venv/bin/python -m applications.sediment_mcp.server`.
That requires a repo clone, a working Python venv, and a locally-minted
dev-token. Engineers only. Doesn't scale.

## Benchmark: Google's `gws` CLI

[`googleworkspace-cli`](https://github.com/googleworkspace/cli) (v0.4.4,
Rust, Apache-2.0) is the cleanest existing model for the same shape of
problem (multi-tenant API exposed as a personal CLI). Eight design points
worth adopting:

| # | Axis | gws choice | Why we copy it |
|---|---|---|---|
| 1 | Distribution | Static Mach-O / ELF binary via Homebrew | One-line install, no Python/Node dependency |
| 2 | Auth UX | `gws auth login` → browser OAuth → encrypted on-disk creds | No JWT copy/paste; survives shell sessions |
| 3 | Multi-account | `--account EMAIL` + `gws auth default` | First-class tenant switching |
| 4 | Env override | `GOOGLE_WORKSPACE_CLI_TOKEN` > creds file > saved OAuth | CI / headless path is explicit |
| 5 | API surface | Mirrors namespace 1:1 (`service resource method`) + `gws schema` | Adding endpoints requires near-zero code |
| 6 | I/O contract | `--params '{JSON}'`, `--json '{JSON}'`, `--format json/table/yaml/csv`, `--page-all` NDJSON | LLM-friendly + shell-pipe friendly |
| 7 | Error envelope | JSON `{error:{code,message,reason}}` on stdout | LLMs can parse and self-correct |
| 8 | Statelessness | No daemon; fresh process → fresh HTTPS per call | Server-side multi-user concurrency is free |

Point 8 is decisive — from the server's perspective, the CLI is just a
plain HTTPS client. No special multi-user code on the server.

## Decision

**Hybrid: CLI primary + thin MCP shim.**

```
                ┌───────────────────────────────────────────┐
                │  Fly: https://hypeproof-sediment.fly.dev  │
                │  (existing platform/langgraph; adds       │
                │   /api/v1/auth/oauth-device only)         │
                └────────────────────▲──────────────────────┘
                                     │ HTTPS + Bearer JWT
              ┌──────────────────────┴──────────────────────┐
              │                                             │
   ┌──────────┴──────────┐                    ┌─────────────┴─────────┐
   │ `sediment` CLI       │                    │ Other consumers:      │
   │  Rust static binary  │                    │   shell scripts       │
   │  Homebrew tap        │                    │   GitHub Actions      │
   │  ~/.config/sediment/ │                    │   Slackbot            │
   │   token (OS keychain)│                    │   any other IDE       │
   │  Subcommands:        │                    └───────────────────────┘
   │   auth (login/list/  │
   │    default/logout/   │
   │    status/export)    │
   │   whoami search ask  │
   │   read recent schema │
   └──────────┬──────────┘
              │ subprocess (stdio JSON)
   ┌──────────┴──────────┐
   │ `sediment-mcp` shim  │   Python or Rust, ~200 LOC
   │  stdio transport     │   5 tools, each shells out to CLI
   │  No token logic      │   No HTTP logic
   └──────────┬──────────┘
              │ MCP stdio
   ┌──────────┴──────────┐
   │ Claude Code session  │
   └─────────────────────┘
```

Rationale:

1. CLI is useful **outside** Claude Code (scripts, CI, bots, other IDEs).
2. OAuth + multi-account UX is strictly better as a CLI than as a
   per-session MCP setup helper.
3. Claude Code still needs MCP for native tool calls + streaming UX —
   but the MCP shim can be ~200 LOC because it owns no state and no I/O
   logic. It just shells out.
4. One backend change ripples once: fix the CLI, MCP shim follows
   automatically.

## Locked choices

| Choice | Decision | Note |
|---|---|---|
| Auth flow | **OAuth 2.0 Device Authorization Grant (RFC 8628)** | Works headless (CI, remote dev box, SSH). Browser still works — CLI prints URL + code, user pastes into any browser anywhere. Falls back gracefully on machines without a default browser. |
| CLI language | **Rust** | Matches gws. Static binary, no runtime deps. `keyring` crate is mature for OS keychain (macOS Keychain / Linux Secret Service / Windows Credential Manager). `reqwest` + `tokio` for async HTTPS. `clap` for arg parsing. |
| Token storage | OS keychain via `keyring` crate | Better than gws's encrypted on-disk file (no master key UX to manage). |
| Multi-account | `--account EMAIL` flag + `sediment auth default --account EMAIL` | Mirrors gws. |
| Env override priority | `SEDIMENT_TOKEN` > `SEDIMENT_CREDENTIALS_FILE` > keychain default account | CI uses the first. |
| MCP shim language | Python (FastMCP) | Faster to write, smaller maintenance surface. Performance not a concern (it's a subprocess wrapper). |
| MCP shim distribution | `pipx install sediment-mcp` from PyPI, or `pip install` inside any venv | Decoupled from the CLI binary. |
| Output formats | `json` (default for LLMs), `table` (default for humans via TTY detection), `yaml`, `ndjson` (for `--page-all`) | |
| Error envelope | `{error:{code,message,reason,hint}}` on stdout, non-zero exit | LLM sees structured error; shell sees exit code. |

## Backend changes (minimal)

| File / endpoint | Change |
|---|---|
| `services/sediment/applications/sediment_platform/routers/auth.py` | Add `POST /api/v1/auth/oauth-device/start` and `POST /api/v1/auth/oauth-device/poll`. RFC 8628 device + user codes. On poll success, mint the same JWT as `/oauth-exchange`. |
| `services/sediment/applications/sediment_platform/middleware/` | Add per-`member_id` rate limit (60/min default, 5 concurrent SSE) on protected routes. |
| `lab_lib/auth.py` | `require_identity` already extracts JWT; add `members.revoked_at` check (cacheable, <5ms p50 budget). |
| `infra/init.sql` | Add `mcp_call_log` table — `(id, member_id, tool, latency_ms, result_bytes, ts)`. Append-only, partitioned by month. RLS by tenant. |
| `services/sediment/Dockerfile` | No change. MCP HTTP transport already exposed on :10030 inside the VM; not externally routed because CLI doesn't need it. |

The existing `/oauth-exchange` endpoint stays — it's still how the web UI
mints tokens via NextAuth.

## What we are NOT doing

- Not exposing remote MCP (HTTP/SSE) over the public internet — CLI
  replaces that need.
- Not introducing refresh tokens in v1 — Device Code returns a 24h JWT;
  `sediment auth login` re-runs the flow when expired. (Add refresh in
  v2 if friction warrants.)
- Not building a Node CLI — Rust single binary is cleaner.
- Not changing tenant/RLS model — same `set_config('app.tenant_id')` per
  request as today.

## Test plan (designed before code)

All tests authored before the implementation they cover. Each tier gates
the next.

### UT — Rust `cargo test` (~30 tests, <50ms each)

**Auth / token**
1. `keyring_roundtrip` — store, fetch, delete a token; entry isolated by `account` key.
2. `token_priority_env_wins` — `SEDIMENT_TOKEN` set → keychain ignored.
3. `token_priority_file_over_keychain` — `SEDIMENT_CREDENTIALS_FILE=...` → file used.
4. `multi_account_isolation` — `--account a@x`, `--account b@y` resolve to different keychain entries.
5. `expired_token_detection` — JWT `exp` < now → CLI surfaces `auth_expired` error code (not stack trace).
6. `device_flow_polls_until_success` — mock provider returns `authorization_pending`, then JWT; CLI polls with `interval` from server.
7. `device_flow_respects_slow_down` — `slow_down` error doubles polling interval.

**Args / JSON parsing**
8. `params_json_valid` — `--params '{"limit":8}'` parses to map.
9. `params_json_invalid_friendly` — `--params 'limit:8'` returns clear error pointing at column.
10. `limit_clamped_at_50` — `--limit 999` silently clamps.
11. `days_clamped` — `--days 100000` doesn't overflow.

**Output**
12. `format_json_roundtrip` — search result → JSON → parsed equivalent.
13. `format_table_truncates_long_excerpts` — table mode caps excerpt at 80 col.
14. `format_yaml_emits_valid_yaml` — output passes `serde_yaml::from_str`.
15. `page_all_emits_ndjson` — one JSON per line, last line has `{"_done":true}` sentinel.
16. `tty_detection_default_format` — stdout is TTY → default `table`; piped → default `json`.

**Error envelope**
17. `http_401_envelope` — server 401 → `{"error":{"code":401,"reason":"auth_expired","hint":"run sediment auth login"}}`.
18. `http_429_envelope_with_retry` — 429 + `Retry-After: 2` → CLI retries once after 2s, then surfaces error.
19. `http_500_envelope` — 500 → exit 1, envelope on stdout, server detail on stderr.
20. `network_error_envelope` — connection refused → `code: -1, reason: network_unreachable`.

**Schema introspection**
21. `schema_search` — `sediment schema search` returns expected JSON Schema for params/result.
22. `schema_matches_server` — schema document hash equals server's OpenAPI excerpt (drift detector).

**Misc**
23. `version_flag` — `--version` matches Cargo.toml.
24. `help_subcommands_complete` — every subcommand has `--help`.
25. `config_dir_xdg_aware` — respects `XDG_CONFIG_HOME` on Linux.
26. `path_traversal_in_read` — `sediment read "../../etc/passwd"` rejected client-side before HTTP.
27. `null_token_no_auth_header` — unauthenticated call sends no `Authorization` (instead of `Bearer null`).
28. `concurrent_calls_no_shared_mut` — 10 parallel `tokio::spawn` calls don't race on token cache.
29. `signal_int_cancels_stream` — Ctrl-C during `ask --stream` cleanly cancels.
30. `non_utf8_response_handled` — server returns garbage bytes → friendly error, no panic.

### IT — `cargo test` + `docker compose` Postgres/platform/langgraph (~15 tests, ~2s each)

1. `dev_token_then_whoami` — via existing `/dev-token`, CLI `whoami` matches.
2. `oauth_device_full_flow` — start → poll → JWT works.
3. `oauth_device_with_github_login` — device flow resolves member by `github_login` not email.
4. **`rls_cross_tenant_isolation_via_cli`** — Tenant-A token searching for a Tenant-B-only term → empty result. **Load-bearing.**
5. `two_accounts_no_token_bleed` — `--account a` and `--account b` interleaved → each sees only its tenant.
6. `revoked_member_token_rejected` — set `members.revoked_at=NOW()` → next call 401.
7. `rate_limit_429_with_retry_after` — 70 calls/min → some 429, header set.
8. `search_then_read_roundtrip` — search returns ref → `read <ref>` body contains the query term.
9. `ask_stream_sse_to_stdout` — `ask --stream` streams deltas; final line is citations JSON.
10. `ask_persists_conversation` — conversation row created, owned by calling member.
11. `ask_disconnect_no_orphan` — kill CLI mid-stream → no orphan langgraph worker after 10s.
12. `page_all_pagination_correct` — `recent --page-all` total = sum of single-page totals.
13. `schema_drift_detector` — `sediment schema search` JSON matches the server's OpenAPI subset.
14. `mcp_call_log_appended` — every call writes one row with correct member_id.
15. `member_revoked_at_p50_budget` — added DB check stays ≤ 5ms p50 over 1000 calls.

### MCP shim IT — pytest (~8 tests)

1. `list_tools_returns_5` — `await mcp.list_tools()` returns exactly `[whoami, search, read, ask, recent]`. Matches `validator/checks/p2_chat.py:check_mcp_tool_count`.
2. `shim_invokes_cli_with_correct_args` — mock subprocess; assert argv per tool.
3. `cli_nonzero_exit_to_mcp_error` — CLI exit 1 → MCP returns error result, not raises.
4. `cli_stdout_json_passthrough` — CLI JSON envelope → MCP tool result equals envelope.
5. `concurrent_tool_calls_no_subprocess_clash` — 2 simultaneous tool invocations spawn independent subprocesses.
6. `cli_not_found_friendly_error` — `which sediment` empty → MCP error with hint to `brew install`.
7. `cli_auth_required_propagates_hint` — CLI returns `auth_expired` → MCP result includes `hint: run sediment auth login`.
8. `tool_schema_byte_equal_to_cli_schema` — MCP tool schema for `search` byte-equals `sediment schema search`.

### E2E — real Fly + real Claude Code (~7 tests, ~30s each)

1. `e2e_brew_install_login_query_one_shot` — `brew install …` → `sediment auth login` (mocked browser) → `claude -p "use sediment__whoami"` exit 0.
2. `e2e_two_users_two_tenants_parallel` — two CC sessions, two different tokens, same query → disjoint citation sets.
3. `e2e_token_expired_clear_error` — manually expire JWT → CC surfaces `auth_expired` not stack trace.
4. `e2e_search_returns_citations` — top-level search produces ≥1 ref.
5. `e2e_ask_synthesized_answer` — `ask` returns answer + ≥1 citation.
6. `e2e_offline_llm_mock_warning_visible` — langgraph mock provider → warning string in CC output.
7. `e2e_schema_introspection_visible_to_llm` — Claude can call `sediment schema search` and use the result to construct a valid call.

### Stress — k6 / locust from a separate host against staging (~5 scenarios)

1. **Steady state**: 10 users × 4 calls/min × 30 min. Target: p50 < 400ms (search), < 3s (ask). Errors < 0.5%. Memory steady.
2. **`ask` burst**: 20 concurrent `ask` calls. SSE all complete, no langgraph OOM.
3. **Search QPS ramp**: 1 → 50 QPS over 5 min. Find knee (expected ~25–30 QPS, Supabase pool 15).
4. **Reconnect storm**: 100 sessions launch within 5s. No 5xx; nginx accept queue holds.
5. **Tenant fairness**: Tenant A drives 80% of load. Tenant B p95 degrades < 2x (rate limit validation).

**CLI-specific micro-benchmark** (run inside stress suite):
- 50 concurrent `sediment search` invocations on one host. Subprocess
  fork cost should be ≤ 10ms each (Rust static binary, no runtime init).

**Cost guardrail**: total stress-run LLM spend ≤ $0.50, enforced by
`ANTHROPIC_USD_DAILY_CAP`.

## CI / scheduling

| Tier | Where | When |
|---|---|---|
| UT | `cargo test` on every PR | already CI |
| IT | `validator/rubric.yaml` (new P2/P3 checks) — `make validate-p2` | every PR + nightly |
| MCP shim IT | pytest in existing `tests/` | every PR |
| E2E | new `.github/workflows/cli-e2e.yml` | post-deploy on main |
| Stress | new `.github/workflows/cli-stress.yml` | weekly Sunday, Discord notification |

## Migration steps (smallest viable order)

1. **Backend**: add `/api/v1/auth/oauth-device/{start,poll}`. UT + IT only — no CLI yet.
2. **IT #4 first**: write `rls_cross_tenant_isolation_via_cli` against a stubbed CLI that mimics the future HTTP shape. Catches RLS regressions before code lands.
3. **Rust CLI v0**: `auth (login/status/logout)`, `whoami`, `search`. Tag `v0.1.0`. Internal dogfood only.
4. **Homebrew tap**: create `hypeprooflab/homebrew-tap` repo. GH Actions cross-compiles darwin-arm64, darwin-x86_64, linux-x86_64.
5. **Rust CLI v1**: `ask --stream`, `read`, `recent`, `schema`, `--page-all`. Tag `v0.2.0`.
6. **MCP shim**: `sediment-mcp` Python package on PyPI. Shells to CLI.
7. **`/sediment-connect` rewrite**: detects `which sediment`; if present and `sediment auth status` is OK, writes MCP json for the shim. Falls back to old venv path otherwise.
8. **Add `members.revoked_at` + rate limit + `mcp_call_log`** behind feature flag.
9. **Soft launch**: Jay + 2 teammates. Monitor `mcp_call_log` for one week.
10. **Run stress suite. Document baselines. GA.**

## Open questions / future work

- **Web-UI parity**: at some point we may want `sediment serve` to embed
  a local web UI for users who prefer browser over CLI for `ask`. Not v1.
- **Plugin model**: Sediment connectors (GitHub, Discord, Notion) could
  ship companion CLI subcommands (`sediment github sync`). Defer until we
  have demand.
- **Windows support**: Rust cross-compiles fine, but device-flow browser
  open + keychain on Windows need a separate test pass. Defer until a
  user asks.
- **Refresh tokens**: see "What we are NOT doing". Revisit if 24h
  re-login becomes friction.

## Appendix: gws design points NOT adopted (and why)

| gws choice | Our choice | Reason |
|---|---|---|
| Encrypted creds file on disk | OS keychain | Better UX, no master-key UX. |
| Curation-free API mirror (`service resource method`) | Curated 5 tools (`whoami/search/ask/read/recent`) + `schema` | Sediment's API is small and stable; curation gives better LLM tool descriptions. Connector-specific subcommands can be added later. |
| Browser loopback OAuth as the only flow | Device Authorization Grant | Works on headless / remote dev boxes. Browser flow could be added as a fast path. |
| Disclaimer "Not an officially supported Google product" | Internal product, formal support tier | HypeProof Lab owns this. |
