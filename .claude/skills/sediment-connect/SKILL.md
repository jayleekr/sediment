---
name: sediment-connect
description: Connect this Claude Code session to Sediment (HypeProof Lab's evidence-grounded memory layer — "where doing becomes knowing") via MCP. Two paths: (1) preferred — `sediment` CLI + `sediment-mcp` shim (one Homebrew install, browser OAuth); (2) legacy — local venv + dev-token (engineers in the repo only).
user_invocable: true
triggers:
  - "/sediment-connect"
  - "/sediment-setup-mcp"
  - "/curator-connect"
  - "connect to sediment"
  - "connect to curator"
  - "register sediment mcp"
---

> **Brand**: Sediment. **Internal codename**: curator. Module paths
> (`services/sediment/`, `applications/sediment_mcp/`) and some env vars
> retain the codename — only user-visible surfaces (MCP tool names, web
> UI, skill name, the new `sediment` CLI) carry the brand.

## Two paths

| Path | Audience | Setup |
|---|---|---|
| **A. CLI + shim** *(preferred)* | Any teammate, any machine | `brew install hypeprooflab/tap/sediment` + `pipx install sediment-mcp-shim` |
| **B. Local venv** *(legacy)* | Engineers with the repo cloned | `services/sediment/.venv` + dev-token |

The agent must detect which path applies and configure accordingly.

## Path A — CLI + MCP shim (preferred)

### What this does

1. **Detect** `sediment` on PATH (`which sediment`). If missing, give the
   user the install command and stop.
2. **Detect** `sediment-mcp` on PATH (the `pipx`-installed shim). If
   missing, give the install command and stop.
3. **Check auth**: `sediment auth status --format json`. If `logged_in`
   is false, prompt the user to run `sediment auth login` (which opens
   a browser via RFC 8628 Device Authorization Grant).
4. **Write MCP config** to `~/.claude/mcp_servers/sediment.json`:
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
   Merge with existing config — do NOT clobber other MCP servers.
5. **Verify**: invoke the shim with `--list-tools` (or run a stub
   `sediment__whoami` via stdio) and report the bound identity.

### Install commands the agent should print verbatim

```bash
# 1. CLI (Rust static binary)
brew install hypeprooflab/tap/sediment

# 2. MCP shim (Python via pipx)
pipx install sediment-mcp-shim

# 3. Log in (opens browser)
sediment auth login
```

### Tools available after connect

| Tool | Use for |
|---|---|
| `sediment__whoami` | verify the token still works |
| `sediment__search` | hybrid retrieval, ranked refs + excerpts |
| `sediment__read`   | fetch one artifact body by ref |
| `sediment__recent` | what's new in the vault (last N days) |
| `sediment__ask`    | natural-language Q&A with citations |

### Multi-account

Users with multiple emails can:
```bash
sediment auth login --account a@x.com
sediment auth login --account b@y.com
sediment auth list                # show both
sediment auth default --account b@y.com   # set default
```
The MCP shim uses whichever account is the CLI default. To pin a
specific account for THIS Claude Code instance, set `SEDIMENT_ACCOUNT`
in the shim's env section of `sediment.json`.

## Path B — Local venv (legacy / repo contributors only)

Identical to the previous version of this skill. Triggered when the user
has the repo cloned at `~/CodeWorkspace/sediment` (or similar) AND has
not installed the `sediment` CLI binary.

1. Resolve `SEDIMENT_BASE_URL` (default `http://localhost:10100`).
2. Mint a token via `/api/v1/auth/dev-token`.
3. Write `~/.claude/mcp_servers/sediment.json` pointing at
   `services/sediment/.venv/bin/python -m applications.sediment_mcp.server`
   with the JWT in env.

This path is documented for completeness; new teammates should use Path A.

## How the agent decides

Pseudocode for `/sediment-connect`:

```python
cli_present = shell_exit("which sediment") == 0
shim_present = shell_exit("which sediment-mcp") == 0
repo_present = file_exists("services/sediment/applications/sediment_mcp/server.py")

if cli_present:
    # Always prefer Path A when the CLI exists.
    if not shim_present:
        prompt_install_shim()
        return
    if shell_exit("sediment auth status --format json | jq -e .logged_in") != 0:
        prompt_login()
        return
    write_mcp_config_path_a()
    verify_via_whoami()
elif repo_present:
    print("CLI not installed — falling back to legacy venv path (recommended: install CLI)")
    do_path_b()
else:
    print("Install the CLI: `brew install hypeprooflab/tap/sediment`")
```

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `which sediment` empty | CLI not installed | `brew install hypeprooflab/tap/sediment` |
| `which sediment-mcp` empty | shim not installed | `pipx install sediment-mcp-shim` |
| `sediment auth status` shows `logged_in: false` | token missing / expired | `sediment auth login` |
| MCP server not visible in CC after connect | CC needs restart to pick up new MCP servers | Cmd+Q, reopen |
| Wrong account bound | multiple emails on this machine | `sediment auth default --account <email>` |

## Notes

- The CLI binary is the source of truth for credentials. The MCP shim
  owns no state.
- Token lifetime is 24h. The CLI does not auto-refresh — re-run
  `sediment auth login` when it expires.
- For CI / headless: set `SEDIMENT_TOKEN` env var directly (highest
  priority override).
