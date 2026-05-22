# CLI Multi-User Access — Test Requirements (full)

> Status: spec
> Scope: the `sediment` Rust CLI, the `sediment-mcp` shim, the backend
> changes that support them (`/oauth-device`, `members.revoked_at`, rate
> limit, `mcp_call_log`).
> Parent doc: [cli-multi-user-access.md](cli-multi-user-access.md)
> Parent project test framework: [../../TEST_REQUIREMENTS.md](../../TEST_REQUIREMENTS.md)

Lists every test, every edge case, every assertion. Tests are tagged
**done** / **todo** / **deferred**. CI uses these tags to gate merges:
all `done` must pass on every PR; `todo` blocks v1.0 GA; `deferred` is
post-GA hardening.

---

## Coverage targets

| Tier | Target | Enforced by |
|---|---|---|
| Rust CLI line cov | ≥ 75% | `cargo tarpaulin` (CI) |
| MCP shim line cov | ≥ 85% | `pytest --cov` (CI) |
| Backend new code line cov | ≥ 85% | `pytest --cov` (CI) |
| RLS cross-tenant invariant | 100% (no exceptions) | `validator/rubric.yaml` P2-RLS-* checks |
| Critical-path E2E | every release tag | post-deploy job |

## Test categories

```
1. UT (unit)              — fast, no I/O, mocked deps
2. IT (integration)       — local Postgres + uvicorn, no network
3. E2E (end-to-end)       — real CLI binary + real platform process
4. Cross-tenant safety    — load-bearing RLS invariants
5. Security               — OWASP LLM Top 10 + injection + auth
6. Performance / load     — latency p95, QPS knee, memory steady
7. Failure-mode / chaos   — network, DB drop, signal, disk
8. Compatibility          — OS, terminal, Python, Rust toolchain
9. Distribution           — Homebrew formula, PyPI metadata
10. Observability         — mcp_call_log fidelity, metrics
```

---

# 1. Unit tests

## 1.1 Backend — device flow + auth (`tests/test_oauth_device.py`)

### Happy path **[done — 5 tests]**
- ✅ `start_returns_codes_and_uris` — payload shape, alphabet, dashed format
- ✅ `poll_pending_returns_authorization_pending`
- ✅ `poll_slow_down_when_polled_too_fast`
- ✅ `poll_unknown_device_code_treated_as_expired`
- ✅ `user_code_uses_safe_alphabet` (BCDFGHJKLMNPQRSTVWXZ, no vowels)

### Full flow **[done — 3 tests]**
- ✅ `full_device_flow_via_approve_dev` — start → pending → approve → mint
- ✅ `replay_device_code_after_consume_rejected` — single-use guarantee
- ✅ `approve_dev_disabled_without_flag` — SEDIMENT_DEV_MODE=1 gate

### Revocation **[done — 1 test]**
- ✅ `revoked_member_blocks_whoami` — cache invalidated, 401

### Edge cases **[todo — 9 tests]**
- ⏳ `expired_device_code_marked_after_poll` — TTL elapsed → status='expired' persisted
- ⏳ `approve_after_expiration_returns_410` — approve a code past expires_at
- ⏳ `approve_already_approved_409` — second approve on same user_code
- ⏳ `denied_status_returns_access_denied` — manual UPDATE status='denied' → poll error
- ⏳ `concurrent_starts_no_user_code_collision` — 50 parallel /start (race the unique idx)
- ⏳ `concurrent_polls_on_same_device_code` — 5 concurrent polls; exactly one mints token
- ⏳ `approve_dev_unknown_email_404`
- ⏳ `approve_with_bad_user_code_format_normalized` — lowercase, no dash → uppercase, dashed
- ⏳ `user_code_collision_eventual_success` — fixture forces 4 collisions, 5th succeeds

### JWT edge cases **[todo — 6 tests]**
- ⏳ `whoami_with_modified_payload_rejected` — tamper signature, expect 401
- ⏳ `whoami_with_expired_jwt_rejected`
- ⏳ `whoami_with_wrong_audience_rejected`
- ⏳ `whoami_with_wrong_issuer_rejected`
- ⏳ `whoami_with_no_exp_rejected`
- ⏳ `whoami_iat_in_future_accepted_with_drift` — ≤30s clock skew tolerated

### Cache behavior **[todo — 3 tests]**
- ⏳ `revoked_check_uses_cache_within_ttl` — second call in <30s shouldn't hit DB
- ⏳ `revoked_check_refreshes_after_ttl` — after 30s, DB hit
- ⏳ `revoked_check_cache_per_member_id_isolated`

## 1.2 Backend — rate limit (`tests/test_rate_limit.py`)

### Happy path **[done — 5 tests]**
- ✅ `first_n_calls_allowed`, `burst_then_denied`, `denied_does_not_consume_more_tokens`,
  `separate_keys_independent`, `refill_after_wait`

### Edge cases **[todo — 7 tests]**
- ⏳ `empty_key_treated_as_anonymous_bucket`
- ⏳ `very_high_per_minute_no_overflow` — per_minute=1_000_000 doesn't NaN
- ⏳ `per_minute_one_strict` — exactly 1 call per minute, 2nd denied
- ⏳ `monotonic_clock_robustness` — patch time, ensure no wall-clock regressions
- ⏳ `concurrent_check_atomicity` — 100 concurrent tasks, allow count ≤ capacity
- ⏳ `memory_growth_bounded` — 10K unique keys, bucket dict ≤ 10K entries
- ⏳ `bucket_cleanup_lru_when_over_threshold` — eviction policy (deferred to v1.1)

## 1.3 Backend — audit log (`tests/test_audit.py` — **todo**)

- ⏳ `audit_log_writes_row_with_correct_fields`
- ⏳ `audit_log_failure_does_not_break_caller` — DB drop → request still 200
- ⏳ `audit_log_no_loop_outside_async_context_drops_silently`
- ⏳ `audit_log_truncates_long_error_reason` — 10K-char message clipped

## 1.4 Rust CLI — UT via integration binary (`tests/unit.rs`)

### Happy path **[done — 10 tests]**
- ✅ `help_lists_all_subcommands`, `version_flag_works`, `schema_command_returns_static_json`,
  `schema_unknown_tool_errors`, `read_rejects_path_traversal_client_side`,
  `read_rejects_absolute_path_client_side`, `network_error_emits_network_unreachable_envelope`,
  `invalid_token_against_unreachable_base_doesnt_panic`, `no_token_produces_auth_envelope_with_hint`,
  `search_query_required`

### Argument parsing edge cases **[todo — 8 tests]**
- ⏳ `empty_query_rejected` — `search ""` returns clap usage error
- ⏳ `search_query_with_unicode_emoji_works` — `search "검색 🔍"` parses
- ⏳ `search_query_with_10k_chars_clamped_or_passed_through`
- ⏳ `path_traversal_url_encoded_rejected` — `read "..%2F..%2Fetc%2Fpasswd"` rejected
- ⏳ `path_traversal_dot_dot_anywhere_rejected` — `read "a/../b"` rejected
- ⏳ `path_traversal_unicode_dots_rejected` — `read "．．/passwd"` (U+FF0E full-width)
- ⏳ `limit_zero_clamped_to_one`
- ⏳ `days_zero_clamped_to_one`

### Output formatting edge cases **[todo — 6 tests]**
- ⏳ `tty_default_is_table` (set isatty true via libc — or test on TTY runner)
- ⏳ `pipe_default_is_json` (stdout to /dev/null → json)
- ⏳ `table_truncates_long_excerpts`
- ⏳ `table_handles_null_cells`
- ⏳ `table_handles_unicode_cjk_with_correct_width` — Korean chars in cell
- ⏳ `ndjson_one_object_per_line_no_extra_whitespace`

### Token storage edge cases **[todo — 7 tests]**
- ⏳ `keyring_roundtrip_in_test_backend`
- ⏳ `keyring_set_overwrites_previous`
- ⏳ `keyring_delete_when_missing_is_noop`
- ⏳ `list_accounts_returns_unique_sorted`
- ⏳ `default_account_falls_back_to_single_keyring_entry`
- ⏳ `default_account_returns_none_when_multiple_no_default_set`
- ⏳ `xdg_config_home_respected`

### HTTP client edge cases **[todo — 6 tests]**
- ⏳ `http_429_includes_retry_after_in_error_message`
- ⏳ `http_503_one_retry_then_surface` (if retry policy added — currently no retry)
- ⏳ `http_redirect_follows` (3xx)
- ⏳ `http_invalid_json_body_clear_error_with_byte_preview`
- ⏳ `http_connection_refused_emits_network_unreachable`
- ⏳ `http_timeout_at_60s_emits_clear_error`

### Date math (`epoch_days_ago` / `civil_from_days`) **[todo — 4 tests]**
- ⏳ `epoch_days_ago_days_0_today`
- ⏳ `epoch_days_ago_30_days_ago_iso_format`
- ⏳ `civil_from_days_handles_year_2000` — leap-year boundary
- ⏳ `civil_from_days_handles_year_1970_epoch_zero`

## 1.5 MCP shim UT (`tests/test_shim.py`)

### Happy path **[done — 10 tests]**
- ✅ `tool_count_is_five`, `whoami_invokes_cli`, `search_passes_limit_and_type`,
  `search_limit_clamped`, `read_passes_ref`, `recent_passes_days_and_limit`,
  `ask_uses_long_timeout_and_passes_query`, `cli_missing_returns_hint`,
  `cli_emits_non_json_surfaced`, `cli_error_envelope_passthrough`

### Edge cases **[todo — 6 tests]**
- ⏳ `cli_returns_bom_prefixed_json_parsed_ok`
- ⏳ `cli_hangs_killed_after_timeout` — stub that sleeps 200s
- ⏳ `cli_returns_huge_payload_10mb_no_oom`
- ⏳ `cli_writes_to_stderr_only_surfaced_with_exit_code`
- ⏳ `path_with_spaces_in_CLI_env_works` — `SEDIMENT_CLI=/path with space/sediment`
- ⏳ `concurrent_tool_calls_run_independent_subprocesses` — 5 parallel `whoami`

---

# 2. Integration tests

## 2.1 Backend route integration (`tests/test_*` against Docker Postgres)

### Done **[8 tests]**
- ✅ Device flow full roundtrip + replay
- ✅ Revoked member rejected
- ✅ Library RLS cross-tenant (5 tests in `test_rls_cross_tenant_via_api.py`)

### Todo **[todo — 6 tests]**
- ⏳ `oauth_exchange_by_github_login_isolated_per_tenant`
- ⏳ `dev_token_disabled_in_prod_when_DEV_MODE_unset` — (currently dev-token is always on; track as v1.1)
- ⏳ `audit_log_appended_on_search_success`
- ⏳ `audit_log_appended_on_search_error` — 401, error_reason set
- ⏳ `audit_log_rls_cross_tenant_isolated` — Tenant A admin can't SELECT Tenant B's rows
- ⏳ `rate_limit_429_with_retry_after_header_set`

### Concurrency **[todo — 4 tests]**
- ⏳ `concurrent_device_starts_under_contention` — 100 parallel /start
- ⏳ `concurrent_logins_same_email_independent_tokens` — different device_codes
- ⏳ `concurrent_search_same_member_under_rate_limit` — exact ratio respected
- ⏳ `concurrent_search_two_tenants_no_pool_starvation`

## 2.2 Rust CLI against live platform (worktree-runs-platform IT)

### Done **[1 test — `e2e_login.rs`]**
- ✅ `e2e_device_login_against_running_platform`

### Todo **[todo — 8 tests]**
- ⏳ `e2e_whoami_then_search_uses_same_keyring_token`
- ⏳ `e2e_search_with_unicode_query` — `search "한국어"` against seeded data
- ⏳ `e2e_read_404_clean_envelope`
- ⏳ `e2e_recent_page_all_emits_ndjson_per_page`
- ⏳ `e2e_ask_stream_to_stdout_one_token_at_a_time` — assert output arrives < 5s for first byte
- ⏳ `e2e_logout_clears_keychain_entry`
- ⏳ `e2e_logout_all_wipes_default_account_file`
- ⏳ `e2e_account_switch_via_flag_overrides_default`

## 2.3 MCP shim IT against live platform

### Done **[4 tests]**
- ✅ `e2e_whoami`, `e2e_search`, `e2e_read`, `e2e_invalid_path_blocked_client_side`

### Todo **[todo — 5 tests]**
- ⏳ `e2e_recent_via_shim`
- ⏳ `e2e_ask_via_shim_collects_full_answer`
- ⏳ `e2e_shim_with_missing_token_returns_auth_envelope`
- ⏳ `e2e_shim_stdio_initialize_then_tool_call_via_real_jsonrpc` — full FastMCP protocol path
- ⏳ `e2e_shim_concurrent_calls_under_rate_limit`

---

# 3. Cross-tenant safety (load-bearing)

These tests MUST pass for every PR touching auth, RLS, or the device flow.
If any of them fails, the CLI ships paused.

### Done **[5 tests]**
- ✅ `acme_token_cannot_see_hypeproof_lab_artifacts`
- ✅ `hypeproof_lab_token_cannot_see_acme_artifacts`
- ✅ `hypeproof_lab_token_sees_own_marker` (sanity)
- ✅ `no_token_rejected`
- ✅ `invalid_token_rejected`

### Todo **[todo — 7 tests]**
- ⏳ `cli_with_tenant_a_token_then_b_token_no_cache_bleed` — switch --account, verify no leftover state
- ⏳ `mcp_shim_with_two_concurrent_tokens_no_subprocess_env_bleed`
- ⏳ `audit_log_query_with_tenant_a_session_does_not_see_tenant_b_rows`
- ⏳ `revoked_member_cant_use_any_endpoint` — search, read, recent, ask, whoami
- ⏳ `expired_jwt_does_not_leak_tenant_via_error_message`
- ⏳ `dev_token_for_other_tenant_email_does_not_grant_first_tenant_access`
- ⏳ `device_code_approved_by_tenant_a_admin_cannot_mint_for_tenant_b_member`

---

# 4. Security tests

## 4.1 Auth / authorization **[todo — 8 tests]**
- ⏳ `path_traversal_url_encoded_in_url_path_blocked_server_side` — `/api/v1/library/..%2F..%2Fpasswd`
- ⏳ `path_traversal_double_encoded_blocked`
- ⏳ `sql_injection_in_search_query_safe` — `q="' OR 1=1 --"` → no extra rows, no error leak
- ⏳ `like_injection_in_browse_filter_safe`
- ⏳ `jwt_alg_none_rejected` — `alg=none` token must 401
- ⏳ `jwt_with_different_alg_RS256_rejected` — we only accept HS256
- ⏳ `oversized_token_rejected` — 10KB JWT → 401 without timing oracle
- ⏳ `oversized_query_rejected` — 100KB query → 413 or clamped

## 4.2 OWASP LLM Top 10 (parent doc lists; CLI-specific subset) **[todo — 4 tests]**
- ⏳ `prompt_injection_via_search_returns_safe` — `q="ignore previous instructions"` doesn't change behavior
- ⏳ `indirect_injection_via_seeded_artifact_doesnt_leak_other_tenant` — poisoned artifact in Tenant A can't make Tenant B's `ask` exfiltrate
- ⏳ `excessive_agency_via_ask_cannot_call_admin_endpoints` — `ask` answers don't trigger destructive actions
- ⏳ `model_dos_via_huge_ask_request_clamped` — 100KB question → clamped/rejected

## 4.3 Transport / TLS **[todo — 3 tests]**
- ⏳ `http_only_base_url_warning_emitted_in_prod_mode` — non-localhost http → eprintln warning
- ⏳ `tls_certificate_pinning_off_by_default` (intentional — Cloudflare front)
- ⏳ `cors_does_not_apply_to_mcp_shim` (shim is server-spawned subprocess, no browser)

## 4.4 Secret hygiene **[todo — 4 tests]**
- ⏳ `keyring_entry_not_world_readable` — Linux Secret Service permissions check
- ⏳ `default_account_file_perms_600` — read-only owner
- ⏳ `token_not_logged_in_audit` — `mcp_call_log` row contains member_id, not token
- ⏳ `token_not_in_error_envelope` — any 401 error mustn't echo the bearer back

---

# 5. Performance / load

## 5.1 Steady state **[todo]**
| Scenario | Target | Tool | Owner |
|---|---|---|---|
| 10 users × 4 calls/min × 30 min | p50 < 400ms (search), p50 < 3s (ask), errors < 0.5%, RSS steady | k6 | Jay |
| 20 concurrent `ask` calls | All SSE complete, no langgraph OOM | k6 | Jay |
| Search QPS ramp 1 → 50 over 5 min | Find knee (expected ~25–30, Supabase pool 15) | k6 | Jay |

## 5.2 Burst / spike **[todo]**
| Scenario | Target |
|---|---|
| 100 sessions reconnect within 5s (VS Code reload) | No 5xx, nginx accept queue holds |
| 1000 `search` over 10s from single member | Rate-limited cleanly, 429 + Retry-After ≥80% of overage |
| 50 concurrent `auth login` (`/oauth-device/start`) | All unique user_codes, no DB deadlock |

## 5.3 Tenant fairness **[todo]**
- Tenant A drives 80% of load → Tenant B p95 latency degrades < 2x.

## 5.4 Tail latency **[todo]**
- p99 < 5x p50 for `search` under steady state. Bigger gap = pool tuning needed.

## 5.5 Cost guardrail **[todo]**
- Total `ask` LLM spend across stress run ≤ $0.50. Enforced via `ANTHROPIC_USD_DAILY_CAP`.

## 5.6 CLI subprocess fork cost **[todo]**
- 50 concurrent `sediment search` invocations on one host: median fork+exec+init ≤ 25ms (Rust static binary).

## 5.7 Memory **[todo]**
- 24h soak test: platform RSS growth < 50 MB; no FD leak.

## 5.8 Revocation check overhead **[todo]**
- Adding `members.revoked_at` check costs ≤ 5ms p50 over 1000 calls vs. baseline without check.

---

# 6. Failure-mode / chaos

## 6.1 Network **[todo]**
- ⏳ `cli_handles_connection_refused_cleanly` (covered in UT — also do for ask/stream)
- ⏳ `cli_handles_tcp_reset_mid_stream` — toxiproxy drops connection after first byte
- ⏳ `cli_handles_slow_loris_attack_against_localhost` — server stuck → CLI times out at 60s
- ⏳ `shim_handles_cli_subprocess_killed_by_OOM_killer`

## 6.2 DB **[todo]**
- ⏳ `device_start_when_postgres_unavailable_returns_503`
- ⏳ `audit_log_write_failure_does_not_break_request`
- ⏳ `rate_limit_works_without_redis` (v1 is in-process; redis backend deferred)

## 6.3 Signal / process **[todo]**
- ⏳ `cli_signal_sigint_during_ask_stream_clean_exit_no_orphan_langgraph_worker`
- ⏳ `cli_signal_sigpipe_when_stdout_closed_no_panic` — `sediment search foo | head -1`

## 6.4 Disk **[todo]**
- ⏳ `cli_when_xdg_readonly_clear_error`
- ⏳ `cli_when_disk_full_writing_default_account_clear_error`

---

# 7. Compatibility

## 7.1 OS **[todo]**
- ⏳ macOS arm64 (primary): all tests pass
- ⏳ macOS x86_64 (Rosetta or native): all tests pass
- ⏳ Linux x86_64 (Ubuntu 22.04 in CI): all tests pass
- ⏳ Windows: v1.1 deferred

## 7.2 Terminal **[todo]**
- ⏳ stdout is TTY → table format
- ⏳ stdout piped → json format
- ⏳ stderr captured separately for login flow

## 7.3 Python (MCP shim) **[todo]**
- ⏳ Python 3.10
- ⏳ Python 3.11 (primary)
- ⏳ Python 3.12

## 7.4 Rust toolchain **[todo]**
- ⏳ MSRV 1.75 declared; CI runs both 1.75 and stable

---

# 8. Distribution

## 8.1 Homebrew **[todo]**
- ⏳ `homebrew/sediment.rb.tmpl` renders to a valid formula
- ⏳ `brew install hypeprooflab/tap/sediment` succeeds on macOS 14 fresh VM
- ⏳ `brew test sediment` passes (runs `--version` + `schema search`)
- ⏳ Formula sha256s match release artifact hashes

## 8.2 PyPI (MCP shim) **[todo]**
- ⏳ `python -m build` produces wheel + sdist
- ⏳ Wheel installs cleanly via `pipx install sediment-mcp-shim`
- ⏳ `sediment-mcp --help` runs after pipx install
- ⏳ Wheel size ≤ 100 KB

## 8.3 Release pipeline **[todo]**
- ⏳ Tag `sediment-cli-v0.1.0` → GH Actions produces 3 tarballs + sha256
- ⏳ Release notes auto-generated
- ⏳ Tag triggers Homebrew tap bump PR (separate repo automation, v1.1)

---

# 9. Observability

## 9.1 `mcp_call_log` fidelity **[todo]**
- ⏳ Every protected endpoint call writes exactly one row
- ⏳ Row contains correct member_id, tenant_id, tool, latency_ms, status_code
- ⏳ Error calls record error_reason
- ⏳ `client` column distinguishes 'cli' / 'web' / 'mcp-shim' / NULL
- ⏳ Async write does NOT block the response

## 9.2 Metrics **[todo, post-GA]**
- Prometheus counters: `sediment_cli_calls_total{tool,status}`
- Histogram: `sediment_cli_latency_seconds{tool}`
- Gauge: `sediment_cli_active_streams`

## 9.3 Alerting **[todo, post-GA]**
- 5xx rate > 1% over 5 min → Discord
- p95 > 5s for `search` over 5 min → Discord
- Daily Anthropic spend > $5 → Discord

---

# 10. CI gating matrix

| Workflow | Triggers | What runs | Blocks merge if fails |
|---|---|---|---|
| `pytest-unit.yml` | every PR | UT (Python + Rust UT via `cargo test --test unit`) | yes |
| `pytest-it.yml` | every PR | Backend IT against ephemeral Postgres | yes |
| `cli-stack-it.yml` | every PR touching `services/sediment-cli/` or `services/sediment-mcp/` | spin up platform + run shim IT | yes |
| `cross-tenant-rls.yml` | every PR touching `lab_lib/`, `routers/`, `init.sql`, `migrations/` | the 5 RLS tests + 7 todo ones | yes |
| `cli-stress.yml` | weekly Sunday + manual | k6 scenarios from §5 | warns to Discord, no block |
| `cli-release.yml` | tag `sediment-cli-v*` | cross-compile + release upload + brew test | required for release |

---

# 11. Open / deferred

- Wiremock-based isolated Rust UT for HTTP paths (currently we rely on real platform). v1.1.
- Redis-backed rate-limit + sliding window. v1.1.
- Refresh tokens (avoid 24h re-login). v1.2.
- Multi-process rate-limit coordination. v1.2.
- Windows platform support. v1.2.
- Wireguard / SSO test that proves the CLI works behind a corporate proxy.

---

*Generated 2026-05-22. Update tags ⏳→✅ as tests land.*
