---
name: sediment-onboard
description: End-to-end install + login + MCP shim setup + verification for the Sediment CLI. For teammates who have NEVER used Sediment from the terminal before. Walks step-by-step, detects what's already done, fixes common environment issues, and confirms each stage actually works. Different from /sediment-connect (which only writes the MCP config — assumes CLI is already installed).
user_invocable: true
triggers:
  - "/sediment-onboard"
  - "/sediment-install"
  - "install sediment"
  - "sediment 설치"
  - "set up sediment cli"
  - "sediment 처음 설치"
---

> **What this skill does**: takes a brand-new teammate (any role) from
> nothing to a working `sediment` CLI + MCP integration in ≤ 5 minutes.
> Idempotent — re-running it is safe and detects what's already done.

## Stages

```
[1. preflight]  Check OS, Homebrew, pipx, Python, Claude Code
                ↓
[2. install]    brew tap → brew install → verify sediment --version
                ↓
[3. login]      sediment auth login → device flow → store JWT
                ↓
[4. shim]       pipx install sediment-mcp-shim → verify
                ↓
[5. claude]     Write ~/.claude/mcp_servers/sediment.json
                ↓
[6. verify]     sediment whoami + a test search → confirm everything works
                ↓
[7. report]     Print bound identity + tool list + next steps
```

## Run

The agent runs each stage as a separate shell command, surfacing errors
in plain language. If a stage is already done, it says so and skips.

### Stage 1 — Preflight

Run these checks; report MISSING items but don't abort yet (some are
optional). Print a summary table.

```bash
uname -s              # Darwin | Linux
which brew            # required for Homebrew install path
which pipx            # required for shim
which python3         # required for pipx + shim
python3 --version     # 3.10+ for pipx
which claude          # optional — proves Claude Code CLI is installed
```

| Tool | Required for | If missing |
|---|---|---|
| `brew` | CLI install via Homebrew | macOS: install from https://brew.sh ; Linux: use the tarball path instead |
| `pipx` | MCP shim install | `brew install pipx && pipx ensurepath` |
| `python3` >= 3.10 | shim runtime | `brew install python@3.11` |
| `claude` | run `/sediment-connect` later | optional — CLI works without Claude Code |

### Stage 2 — Install CLI

```bash
# Already installed?
if command -v sediment >/dev/null 2>&1; then
  echo "sediment already installed: $(sediment --version)"
else
  brew tap hypeprooflab/tap
  brew install sediment
fi

# Verify
sediment --version
```

**Failure modes**:
- `tap not found` → tap doesn't exist yet (Homebrew Tap publish is gated).
  Fallback: download tarball from
  https://github.com/hypeprooflab/sediment/releases/latest and `mv
  sediment /usr/local/bin/`.
- `Permission denied` on `/usr/local/bin` → suggest `sudo mv` or use
  `~/.local/bin` and update PATH.

### Stage 3 — Login

```bash
# Already logged in?
sediment auth status --format json 2>/dev/null | jq -e '.logged_in' >/dev/null 2>&1
case $? in
  0) echo "Already logged in as $(sediment auth status --format json | jq -r .account)";;
  *) sediment auth login;;
esac
```

The CLI prints a verification URL + user code, opens a browser, and
polls until the user approves on the web. **If browser doesn't open**
(headless box / SSH), copy the URL manually to any browser.

**Special: multi-account**. If user wants more than one account:
```bash
sediment auth login --account a@example.com
sediment auth login --account b@example.com
sediment auth default --account a@example.com  # pick primary
```

### Stage 4 — MCP shim

```bash
if command -v sediment-mcp >/dev/null 2>&1; then
  echo "sediment-mcp shim already installed"
else
  pipx install sediment-mcp-shim
fi

# Verify the shim can reach the CLI binary
sediment-mcp --help 2>&1 | head -5 || true   # not all FastMCP servers respond to --help
```

### Stage 5 — Register with Claude Code

Write `~/.claude/mcp_servers/sediment.json`:

```json
{
  "mcpServers": {
    "sediment": {
      "command": "sediment-mcp",
      "args": []
    }
  }
}
```

**Merge, don't clobber**: if the file already exists, read it, merge
under `mcpServers.sediment`, write back. Preserve every other key.

After writing, remind the user that **Claude Code must restart** to
pick up new MCP servers (Cmd+Q then reopen).

### Stage 6 — Verify

```bash
sediment whoami --format json | jq
sediment search "research" --limit 1 --format json | jq '.items[0].ref // "(no items)"'
```

Both should print real values (not error envelopes). If either fails,
print the error and a hint.

### Stage 7 — Report

Display a short summary card:

```
✅ Sediment CLI is ready

  Account:      jay.lee@sonatus.com
  Tenant:       hypeproof-lab
  CLI path:     /opt/homebrew/bin/sediment
  Shim path:    /opt/homebrew/bin/sediment-mcp
  MCP config:   ~/.claude/mcp_servers/sediment.json

Try these:
  sediment search "your topic" --limit 5
  sediment ask "what's our 0→1 fit?" --stream
  sediment recent --days 7

In Claude Code (after restart), ask:
  "Use sediment__search to find recall@3 entries."

Docs:
  README:    https://github.com/hypeprooflab/sediment/tree/main/services/sediment-cli
  Quickstart: docs/sediment-cli-quickstart.md
```

## Failure recovery

When the user re-runs `/sediment-onboard` after a failure, the skill
must detect which stage failed and pick up there — not redo everything.
Each stage's check at the top is idempotent.

## Arguments

```
/sediment-onboard [--account EMAIL] [--skip-claude-code]
```

- `--account EMAIL`: pre-bind login to this address (skips picker UI).
- `--skip-claude-code`: stop after stage 4 — for users who don't have
  Claude Code installed.

## Common environment fixes

| Symptom | Auto-fix |
|---|---|
| `brew: command not found` (macOS) | print install URL https://brew.sh ; ask user to run + re-invoke /sediment-onboard |
| `pipx: command not found` | `brew install pipx && pipx ensurepath` then re-invoke |
| `~/.claude` doesn't exist | `mkdir -p ~/.claude/mcp_servers` |
| `Permission denied` writing keychain (Linux) | install `gnome-keyring` or fall back to `SEDIMENT_TOKEN` env path |
| Browser doesn't open during login | print the verification URL and instruct manual paste |

## Telemetry

This skill performs **no** outbound calls except the CLI's own (which
talks only to the configured `SEDIMENT_BASE_URL`). No data is collected
about who ran the skill or when.

## Related skills

- `/sediment-connect` — just writes the MCP json (assumes CLI installed)
- `/sediment-onboard` — this skill, runs the full install + login + verify
