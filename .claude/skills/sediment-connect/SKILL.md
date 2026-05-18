---
name: sediment-connect
description: Connect this Claude Code session to Sediment (HypeProof Lab's evidence-grounded memory layer — "where doing becomes knowing") via MCP. Mints a JWT, writes MCP config, verifies the connection. Run once per machine (and again whenever the token expires).
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
> (`services/sediment/`, `applications/sediment_mcp/`) and env vars
> (`SEDIMENT_TOKEN`, `SEDIMENT_BASE_URL`) retain the codename to avoid a
> larger refactor — only user-visible surfaces (MCP tool names, web UI,
> skill name) carry the brand name.

## What this does

1. Mints a JWT against the Curator dev-token endpoint (Phase 5: Discord OAuth).
2. Writes `~/.claude/mcp_servers/curator.json` so Claude Code spawns the
   `curator_mcp` stdio server with the token in env.
3. Calls `sediment__whoami` to verify the connection and prints the bound
   identity.

After running this, the following MCP tools are available in *any* Claude
Code session on this machine (any worktree):

| Tool | Use for |
|---|---|
| `sediment__ask`     | natural-language Q&A with citations |
| `sediment__search`  | hybrid retrieval, ranked refs+excerpts |
| `sediment__read`    | fetch one artifact body by ref |
| `sediment__recent`  | what's new in the vault (last N days) |
| `sediment__whoami`  | verify the token still works |

## Arguments

```
/curator-connect [email]

email  : seeded member email (default: jay.lee@sonatus.com)
```

## How the agent should run this

1. **Pre-flight**:
   - Resolve `SEDIMENT_BASE_URL` — default `http://localhost:10100`. If the
     user sets a remote URL (e.g. `https://curator.hypeproof-ai.xyz`), use
     that and skip dev-token (use Discord OAuth instead — Phase 5).
   - Resolve `SEDIMENT_LG_BASE` — default `http://localhost:10020`.
   - Pick the email — argument override OR ask the user.

2. **Mint token** (local dev only):
   ```bash
   TOKEN=$(curl -s -X POST "$SEDIMENT_BASE_URL/api/v1/auth/dev-token" \
     -H "Content-Type: application/json" \
     -d "{\"email\":\"$EMAIL\"}" \
     | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
   ```
   If response is missing the token, fail with the API error body.

3. **Resolve `curator_mcp` entry point**:
   - `services/sediment/.venv/bin/python` (absolute path)
   - `-m applications.sediment_mcp.server`
   - cwd: `products/sediment/services/sediment`
   - All paths must be absolute in the MCP config so CC can spawn from any cwd.

4. **Write MCP config** to `~/.claude/mcp_servers/curator.json`:
   ```json
   {
     "mcpServers": {
       "curator": {
         "command": "<absolute path to .venv/bin/python>",
         "args": ["-m", "applications.sediment_mcp.server"],
         "cwd": "<absolute path to services/sediment>",
         "env": {
           "SEDIMENT_BASE_URL": "<resolved URL>",
           "SEDIMENT_LG_BASE": "<resolved URL>",
           "SEDIMENT_TOKEN": "<minted JWT>"
         }
       }
     }
   }
   ```
   Merge with existing config — do NOT clobber other MCP servers the user
   may have configured.

5. **Verify**:
   ```bash
   SEDIMENT_TOKEN="$TOKEN" \
   <python> -c "
   import asyncio
   from applications.sediment_mcp.server import sediment__whoami
   print(asyncio.run(sediment__whoami()))
   "
   ```
   Expected: dict with `member_id`, `tenant_id`, `display_name`.

6. **Report to user**:
   - Identity bound (display_name + tenant_id)
   - Tool list (5 tools)
   - Restart hint — Claude Code picks up MCP changes on next session start.

## Notes

- The dev-token endpoint is local-dev-only. In production, Discord OAuth
  flow mints the JWT.
- Token expiry is 24h by default. Re-run this skill to refresh.
- One token per machine — switching emails replaces the bound identity for
  ALL Claude Code sessions on this machine.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `curl: connection refused` | Curator services not running | `make services-up` in `products/sediment/` |
| `404 member not found` | DB not seeded for this email | `make seed` |
| `sediment__whoami returns error` | Token written wrong / wrong tenant | Re-run skill |
| `MCP server not visible in CC` | Need to restart Claude Code | Cmd+Q, reopen |
