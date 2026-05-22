# Sediment CLI

`sediment` is a single static binary that lets any HypeProof Lab teammate
query the team's evidence-grounded memory ("Sediment") from the command
line — and from any Claude Code session — without cloning this repo.

```bash
brew install hypeprooflab/tap/sediment
sediment auth login         # browser OAuth via RFC 8628 device flow
sediment whoami
sediment search "recall@3"
sediment ask "what's our 0→1 fit?" --stream
```

---

## Why a CLI?

Sediment is a multi-tenant memory layer. Engineers reach it from the web
UI, Claude Code (via MCP), or scripts (via this CLI). The CLI is the
common foundation: it owns the token, talks HTTPS to the Fly-hosted API,
and exposes a small set of high-leverage verbs. The MCP shim
(`sediment-mcp-shim`) shells out to this binary, so any improvement here
flows through to Claude Code automatically.

Design rationale + benchmark vs Google's `gws` CLI:
[`docs/design/cli-multi-user-access.md`](../../docs/design/cli-multi-user-access.md)

---

## Install

### macOS / Linux (Homebrew)
```bash
brew tap hypeprooflab/tap
brew install sediment
```

### From release archive
Grab the right tarball for your OS from the [latest release][releases]:

```bash
# Apple Silicon
curl -L https://github.com/hypeprooflab/sediment/releases/latest/download/sediment-aarch64-apple-darwin.tar.gz | tar xz
mv sediment /usr/local/bin/

# Linux x86_64
curl -L https://github.com/hypeprooflab/sediment/releases/latest/download/sediment-x86_64-unknown-linux-gnu.tar.gz | tar xz
mv sediment /usr/local/bin/
```

[releases]: https://github.com/hypeprooflab/sediment/releases

### From source
```bash
git clone https://github.com/hypeprooflab/sediment
cd sediment/services/sediment-cli
cargo build --release
cp target/release/sediment /usr/local/bin/
```

---

## First-time setup (60 seconds)

```bash
sediment auth login
```

This prints a short user code and opens your browser. Sign in with GitHub
on the verification page, confirm the code, and the CLI mints a JWT and
saves it in your OS keychain (macOS Login Keychain / Linux Secret
Service / Windows Credential Manager).

Verify it worked:
```bash
sediment whoami
```

You should see your display name, member id, and tenant.

---

## The verbs

| Command | What it does |
|---|---|
| `sediment auth login` | OAuth device flow → store JWT |
| `sediment auth status` | Show bound identity + URL |
| `sediment auth list` | Show all accounts stored on this machine |
| `sediment auth default --account EMAIL` | Set the default account when `--account` is omitted |
| `sediment auth logout [--all]` | Remove credentials |
| `sediment whoami` | Identity check |
| `sediment search "query" [--limit N] [--type TYPE]` | Hybrid search; ranked refs + excerpts |
| `sediment read REF` | Fetch one artifact body |
| `sediment recent [--days N] [--limit N] [--page-all]` | What's new in the vault |
| `sediment ask "question" [--stream]` | LLM Q&A with citations |
| `sediment schema TOOL` | JSON schema for a verb (used by the MCP shim) |

### Output formats

`--format` controls output. Auto-detect: `json` when stdout is piped,
`table` when it's a TTY.

| Format | Use for |
|---|---|
| `json` | LLM / script — pretty JSON |
| `table` | Human terminal viewing |
| `yaml` | Eyeballing nested structures |
| `ndjson` | `--page-all` (one page per line) |

---

## Multiple accounts

```bash
sediment auth login --account a@example.com
sediment auth login --account b@example.com
sediment auth list
sediment auth default --account a@example.com
sediment --account b@example.com whoami    # per-call override
```

A separate JWT is stored per account. The default is whichever account
you logged into first, unless overridden.

---

## Env overrides

Priority: `--flag` > env > stored default.

| Variable | Purpose |
|---|---|
| `SEDIMENT_TOKEN` | Raw JWT (highest priority — used by CI / one-shot scripts) |
| `SEDIMENT_CREDENTIALS_FILE` | Path to a `{token, account}` JSON file |
| `SEDIMENT_BASE_URL` | Override the API URL (default: prod) |
| `SEDIMENT_ACCOUNT` | Default account email |
| `SEDIMENT_DEV_MODE` | Treat `SEDIMENT_BASE_URL` default as localhost:10100 |
| `GITHUB_TOKEN` | Required by `sediment update` — the Sediment repo is private, so the GitHub Release API needs auth. Set via `export GITHUB_TOKEN=$(gh auth token)`. |

---

## Self-update

```bash
GITHUB_TOKEN=$(gh auth token) sediment update         # download + replace if newer
GITHUB_TOKEN=$(gh auth token) sediment update --check # report only
GITHUB_TOKEN=$(gh auth token) sediment update --force # reinstall same version
```

The update flow:

1. GET `api.github.com/repos/jayleekr/sediment/releases/latest` (needs PAT)
2. Compare tag (`sediment-cli-vX.Y.Z`) to `--version`
3. If newer (or `--force`), download the right tarball for your triple via the api.github.com asset URL (`application/octet-stream` + Bearer)
4. Verify sha256 against the asset's `digest` metadata
5. Atomic-rename over the running binary (Unix preserves the old inode for the running process; new lookups get the new file)

No telemetry, no daemon, no background polling. The user invokes it.

---

## Using from Claude Code

Install the MCP shim once:
```bash
pipx install sediment-mcp-shim
```

Register with Claude Code (one-time per machine):
```bash
# from inside Claude Code, run:
/sediment-connect
```

After that, any Claude Code session can call:
- `sediment__whoami`
- `sediment__search(query, limit, type)`
- `sediment__read(ref)`
- `sediment__recent(days, type, limit)`
- `sediment__ask(query)`

Tokens, retries, and account switching are all owned by this CLI — the
shim just shells out to it.

---

## Error envelopes

Every failure is a JSON object on stdout with a non-zero exit code:

```json
{
  "error": {
    "code": 401,
    "reason": "auth_expired",
    "message": "HTTP 401: ...",
    "hint": "run `sediment auth login`"
  }
}
```

| Exit code | Class | Typical reason |
|---|---|---|
| 0 | success | |
| 1 | generic | network unreachable, internal |
| 2 | auth | 401 — token missing or expired |
| 3 | permission | 403 — gated endpoint |
| 4 | not_found | 404 — bad ref |
| 5 | rate_limited | 429 — backoff and retry |

Reasons are stable identifiers (`auth_expired`, `not_found`,
`rate_limited`, `network_unreachable`, `path_traversal`, ...). Scripts
can branch on them safely.

---

## Examples

```bash
# Pipe to jq
sediment search "memory consolidation" --limit 5 | jq '.items[].ref'

# Page through everything from the last 90 days
sediment recent --days 90 --limit 100 --page-all | jq -s 'add | .items[].ref'

# Multi-account from a single shell
for acct in a@x b@x; do sediment --account "$acct" whoami; done

# CI: mint a token, run, exit non-zero on auth fail
SEDIMENT_TOKEN="$JWT" sediment search "release" --limit 1 || echo "auth failed: $?"
```

---

## Troubleshooting

| Symptom | Try |
|---|---|
| `not logged in` after install | `sediment auth login` |
| `auth_expired` on every call | JWTs are 24h — re-run `sediment auth login` |
| `keyring: failed to open` on Linux | install `gnome-keyring` or `kwallet`; or use `SEDIMENT_TOKEN` env |
| `network_unreachable` | check `SEDIMENT_BASE_URL`; default expects the prod fly.dev URL |
| Output looks like garbage in scripts | force JSON: `sediment --format json …` |

---

## Development

```bash
cargo build && cargo test
cargo run -- --help
```

Tests are split into:
- `tests/unit.rs` — happy-path UT (10 tests)
- `tests/edges.rs` — edge cases (13 tests; path traversal, unicode, formats)
- `tests/e2e_login.rs` — full device-flow login against a running platform
- `tests/e2e_full.rs` — every verb against prod-like (9 tests)

Run E2E:
```bash
SEDIMENT_E2E_BASE_URL=http://localhost:10101 cargo test --test e2e_full
```

CI runs all four suites + `cargo fmt` + `cargo clippy -D warnings` +
`cargo audit` on every PR (`.github/workflows/cli-tests.yml`).

---

## Privacy

The CLI talks to **only** the URL you set in `SEDIMENT_BASE_URL` (default
the prod Sediment API). No telemetry. The User-Agent string is
`sediment-cli/<version>`.

Tokens live in your OS keychain. They never appear in `~/.config` files
or shell history. `sediment auth logout` removes them; `--all` wipes
every account at once.

---

## License

Apache-2.0.
