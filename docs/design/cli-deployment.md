# CLI Multi-User Access — Deployment Requirements

> Status: spec
> Parent: [cli-multi-user-access.md](cli-multi-user-access.md)
> Audience: anyone shipping a new version of the CLI / shim / backend

What needs to be true before each component reaches users.

---

## 0. Component → artifact → channel

| Component | Artifact | Channel | Cadence |
|---|---|---|---|
| Backend (`/oauth-device`, revoked_at, rate limit, mcp_call_log) | Fly image | `fly deploy` from main (existing `fly-deploy.yml`) | per-commit |
| DB schema | `infra/migrations/NNN_*.sql` | `make migrate-prod` (gated) | per-migration |
| Rust CLI | 3 tarballs + sha256 | GitHub Release on tag `sediment-cli-v*` | per-tag |
| Homebrew formula | `Formula/sediment.rb` in `hypeprooflab/homebrew-tap` | manual PR (v1) → auto (v1.1) | per-tag |
| MCP shim | wheel + sdist on PyPI | `twine upload` or trusted publisher | per-tag |
| `/sediment-connect` skill | repo `.claude/skills/sediment-connect/SKILL.md` | shipped with worktree, picked up by users on `git pull` | per-merge |

Three artifacts have **independent versioning**: CLI, shim, backend. They
interop via JSON schemas, not Python imports, so they can drift one minor
version without breaking. Two-version skew across the matrix is the
support window.

---

## 1. Backend deployment requirements

### 1.1 Pre-deploy gate
| Check | Tool |
|---|---|
| All UT pass (Python) | `make test` |
| All IT pass (Postgres) | `pytest tests/test_oauth_device.py tests/test_rls* tests/test_rate_limit.py` |
| All RLS cross-tenant tests pass | listed in `cross-tenant-rls.yml` |
| `make lint-sql` clean (no `:N::T` SQL casts) | wired into `ai-commit.sh gate` |
| Migration applied to staging successfully + reverted in dry-run | `make migrate --dry-run` then `make migrate` |
| No new secrets in repo | `gitleaks detect` |

### 1.2 Migration policy
- Forward-only. Every migration is idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
- Migrations apply BEFORE the new image is rolled out. Sequence:
  1. Apply migration to prod DB via `make migrate-prod` (or `fly ssh console`).
  2. Wait 60s for queries to drain on old schema.
  3. `fly deploy`. New code reads new columns/tables.
- Rollback: if migration is destructive (it shouldn't be in this PR), keep
  a paired `NNN_*.rollback.sql`. For additive migrations (this one),
  rollback = revert the deploy; new columns/tables stay, harmlessly unused.

### 1.3 Required env / secrets on Fly
| Var | Source | New? |
|---|---|---|
| `DATABASE_URL` | Supabase pooler URL | existing |
| `JWT_SECRET` | Fly secret | existing — DO NOT rotate without coordinated CLI re-login |
| `ANTHROPIC_API_KEY` | Fly secret | existing |
| `SEDIMENT_DEV_MODE` | unset in prod | **new — must NOT be set in prod, else `/oauth-device/approve-dev` is exposed** |
| `PUBLIC_BASE_URL` | `https://sediment.hypeproof-ai.xyz` | new — used by `verification_uri` |
| `QUERY_RATELIMIT_PER_MIN` | default 20, override per env | existing — confirm tuned per tier |

### 1.4 Smoke checks post-deploy (run by `fly-deploy.yml`)
| Endpoint | Expectation |
|---|---|
| `GET /healthz` | 200 |
| `POST /api/v1/auth/oauth-device/start` | 200 with valid `device_code` + `user_code` |
| `POST /api/v1/auth/oauth-device/approve-dev` | **403** (dev mode off) |
| Existing nightly recall@3 | ≥ 20/40 PASS |

### 1.5 Observability touch-points
- New audit table `mcp_call_log` — Discord alert if 0 inserts in 1h after launch (means writer regressed)
- Daily aggregation: per-tenant call count, per-tool latency p50/p95
- Postgres connection pool gauge — alert at 80% utilization

---

## 2. Rust CLI release process

### 2.1 Pre-release gate
| Check | Tool |
|---|---|
| `cargo fmt --check` | rustfmt |
| `cargo clippy --all-targets -- -D warnings` | clippy |
| `cargo test --all` passes | cargo test |
| `cargo audit` shows no high/critical advisories | cargo-audit |
| Cross-compile dry-run for all 3 targets | local |
| Manual smoke on macOS arm64 + Linux x86_64 | local |
| Version bumped in `Cargo.toml` |  |
| `CHANGELOG.md` updated (todo file in v1.1) |  |

### 2.2 Release flow (the happy path)
```bash
# 1. bump version
sed -i '' 's/version = ".*"/version = "0.2.0"/' services/sediment-cli/Cargo.toml
cargo build --release  # update Cargo.lock

# 2. commit + tag
git commit -am "chore(cli): release v0.2.0"
git tag -a sediment-cli-v0.2.0 -m "v0.2.0"
git push origin main --tags

# 3. GH Actions (.github/workflows/sediment-cli-release.yml) auto-runs:
#    - builds 3 targets
#    - uploads to GitHub Release sediment-cli-v0.2.0
#    - generates release notes

# 4. (v1 manual) Update Homebrew formula
#    Compute sha256 from release artifacts:
shasum -a 256 dist/sediment-aarch64-apple-darwin.tar.gz
#    Render homebrew/sediment.rb.tmpl with the new version + sha256s
#    Open PR against hypeprooflab/homebrew-tap

# 5. Announce in Discord #sediment-releases
```

### 2.3 Backwards-compat policy
- The CLI calls `/api/v1` endpoints — those are stable contracts.
- New CLI minor versions MUST work against the previous backend minor
  version (N-1 support window).
- Token format (HS256 JWT) is owned by the backend. CLI treats it as
  opaque.
- `sediment schema X` output is a contract for the MCP shim — any change
  is a breaking change for shim's tool descriptions.

### 2.4 Distribution checklist
- [ ] Binary is statically linked (no `dyld` warnings on macOS `otool -L`)
- [ ] No `.dSYM` / debug info bloats the tarball (release profile strips)
- [ ] Linux binary built on Ubuntu 22.04 to match glibc baseline
- [ ] macOS binaries codesigned? — v1.1, currently unsigned (user sees Gatekeeper prompt). Document in install instructions.
- [ ] No telemetry phone-home (verify no `reqwest` calls except to user's configured base_url)

---

## 3. MCP shim release process

### 3.1 Pre-release gate
- `pytest` passes (unit + e2e against staging)
- Version bumped in `pyproject.toml`
- Wheel + sdist build cleanly: `python -m build`

### 3.2 Release flow
```bash
# 1. bump version
sed -i '' 's/version = "[^"]*"/version = "0.2.0"/' services/sediment-mcp/pyproject.toml

# 2. build + upload
cd services/sediment-mcp
python -m build
twine upload dist/*

# 3. Smoke from a clean env
pipx uninstall sediment-mcp-shim || true
pipx install sediment-mcp-shim==0.2.0
sediment-mcp --help
```

### 3.3 Trusted publisher (v1.1)
Configure PyPI's GitHub OIDC trusted publishing so we don't need a token
in CI. Until then, store `PYPI_API_TOKEN` as a GH Actions secret.

---

## 4. Homebrew tap setup (one-time)

```bash
# Create the tap repo (one-time, outside this codebase)
gh repo create hypeprooflab/homebrew-tap --public --description "Homebrew tap for HypeProof Lab tools"
cd /tmp && gh repo clone hypeprooflab/homebrew-tap
mkdir Formula
# Drop services/sediment-cli/homebrew/sediment.rb.tmpl into Formula/sediment.rb
# Fill in version + sha256s for the v0.1.0 release artifacts
git add Formula/sediment.rb
git commit -m "Initial Sediment formula"
git push
```

After this, users install with `brew install hypeprooflab/tap/sediment`.

For each subsequent CLI release: bump version + sha256s in the formula,
PR, merge.

**v1.1 automation**: GH Actions in the tap repo that reads the latest
release tarballs from `hypeprooflab/sediment` and opens an auto-PR.

---

## 5. Roll-out plan

### 5.1 Soft launch (week 1)
- 3 internal users (Jay + 2 teammates)
- Manual install: tarball download + symlink (skip Homebrew until tap exists)
- Daily check of `mcp_call_log` for anomalies
- Each user reports any issue in Discord #sediment

### 5.2 Beta (week 2-3)
- Open to all 9 HypeProof Lab members
- Homebrew tap published
- Slack/Discord pinned install instructions
- Stress test §5 of cli-test-requirements.md runs weekly

### 5.3 GA (week 4)
- README / SPEC.md updated to mark CLI as the primary access path
- Web UI auth flow links to CLI install for "advanced users"
- Decommission `/sediment-connect` Path B (legacy venv) — 30d notice
  posted, then path removed

### 5.4 Hold criteria (revert/pause launch)
- Cross-tenant RLS test ever fails on main
- p95 latency on `search` > 2s sustained for 1h
- 5xx rate > 2% over any 15-min window
- Daily Anthropic spend exceeds $5 unexpectedly
- Any keychain/credential write fails on user's machine (corrupted creds)

---

## 6. Capacity planning (v1)

| Resource | Current | Headroom | Trigger to scale |
|---|---|---|---|
| Fly VM | 1× shared-cpu-1x | ~50 concurrent SSE streams comfortably | p95 > 2s sustained |
| Supabase Postgres pool | 15 connections | ~25 QPS sustained | Pool exhaustion in logs |
| `query_ratelimit_per_min` | 20 | enough for 1 user; teammates collectively ~100/min | When stress §5 shows individuals hitting cap routinely |
| Anthropic `ANTHROPIC_USD_DAILY_CAP` | $5 | ~50 `ask` calls/day | Daily spend > $4 sustained |

Scale-up sequence when triggered:
1. Bump Supabase pool to 25 (Supabase dashboard, no DB restart).
2. Increase Fly VM size: `fly scale vm shared-cpu-2x`.
3. If still constrained, add Redis-backed rate limit (decoupled from
   process count) and run 2 Fly VMs.

---

## 7. Monitoring + SLOs

### 7.1 SLOs (initial proposal — tune after 30d data)
- **Availability**: 99% monthly. Budget = 7.2 hours/month down.
- **Search p95 latency**: < 800ms
- **Ask first-byte latency p95**: < 4s
- **Token mint p99**: < 1s
- **Cross-tenant RLS leaks**: ZERO. Any leak is sev-1.

### 7.2 Dashboards (v1.1)
- Grafana board: latency histograms per tool, member-level QPS, error budget burn.
- Daily Discord post from a cron summarizing yesterday's metrics.

### 7.3 Pager
- Sev-1: RLS leak detected, Postgres unreachable, JWT secret rotated
  unintentionally → Jay direct DM.
- Sev-2: SLO burn > 50% in 24h → Discord channel ping.
- Sev-3: cost cap warnings → Discord channel.

---

## 8. Security review checklist

Before announcing CLI publicly (even to teammates):

- [ ] Penetration test on `/oauth-device/*` against a staging instance
- [ ] `SEDIMENT_DEV_MODE` is unset in prod Fly secrets
- [ ] `JWT_SECRET` has 256 bits of entropy and is rotated annually
- [ ] Audit table grants are minimum-privilege (`curator_app` SELECT/INSERT only)
- [ ] `mcp_call_log` retention policy: 90 days, then aggregated
- [ ] OWASP LLM Top 10 #1 (prompt injection) covered by §4.2 of test reqs
- [ ] Token never logged in plaintext (verified by §4.4 of test reqs)
- [ ] Keyring entry permissions audited per OS
- [ ] Release artifacts uploaded over HTTPS with sha256 checksum sidecars

---

## 9. Decommission of legacy paths

When CLI Path A reaches >90% of MAU:

1. Mark `/sediment-connect` Path B (local venv) deprecated in SKILL.md.
2. Add `Deprecation: true` HTTP response header on `/api/v1/auth/dev-token`.
3. 30-day notice posted in Discord.
4. Delete `applications/sediment_mcp/server.py` Python MCP server.
   (Keep the audit log of who used it via `mcp_call_log.client = 'mcp-python'`.)
5. Drop `client_id` rows from `device_authorization_codes` older than 7d
   via a daily cron.

---

*Last updated: 2026-05-22.*
