---
name: curator:setup
description: One-shot environment setup — Docker credential PATH, Python venv, deps install, playwright chromium, .env scaffold. Idempotent. Self-healing — Ralph invokes this when env breaks.
user_invocable: true
triggers:
  - "/curator:setup"
  - "curator setup"
  - "env broken"
---

## Purpose

The harness assumes a working dev environment (Docker Desktop, Python 3.11+,
Node, deps installed). When any of those drift (Mac update, dep changes, fresh
clone, sleep/wake), this skill restores them in idempotent steps.

Ralph dispatches this when:
- `make seed` returns ModuleNotFoundError
- `docker compose up` fails with credential helper missing
- Playwright import fails inside an E2E check
- venv Python version mismatch detected

## What it fixes (8 idempotent stages)

| Stage | Check | Fix |
|---|---|---|
| 1 | Docker daemon reachable | print "open -a Docker" + wait 20s + retry |
| 2 | docker-credential-desktop in PATH | export PATH= /usr/local/bin:/Applications/Docker.app/Contents/Resources/bin |
| 3 | Python 3.11+ available | use /opt/homebrew/bin/python3.11 or python3.13 fallback |
| 4 | venv exists with right Python | recreate if version mismatch |
| 5 | core deps installed (fastapi, sqlalchemy, langgraph) | `pip install -e .[dev]` if missing |
| 6 | playwright chromium installed | `playwright install chromium` if missing |
| 7 | .env exists with API keys | copy from .env.example; warn if keys still placeholders |
| 8 | infra containers running | `docker compose up -d` if down |

## Workflow

```bash
bash products/sediment/harness/scripts/setup-env.sh
```

The bash script (under `harness/scripts/`) implements the 8 stages above.
Each stage exits 0 if already-healthy (idempotent skip).

After running, summarize to user:
- Stages that ran fresh
- Stages that skipped
- Anything still missing (e.g., API keys)

## Output

Caller (Jay or Ralph) gets:
```
✓ docker daemon
✓ docker credentials (PATH patched)
✓ python 3.11.13
✓ venv (/Users/jaylee/.../services/sediment/.venv)
✓ deps (fastapi 0.115, sqlalchemy 2.x, langgraph 0.2.x)
○ playwright chromium MISSING — running install (~150MB)
✓ .env — but API keys are placeholders (offline mode)
✓ docker compose: postgres + redis up
```

## Hard rules

- Never modify `~/.docker/config.json` (Jay's machine state).
- Never delete the venv unless Python version mismatch is confirmed.
- Never overwrite `.env` if it exists and has real (non-`sk-...`) keys.
- Always log to `output/setup/setup-<ts>.log` for postmortem.

## Cross-project portability

Project-agnostic by design. Used as the "L0" recovery layer in the harness
manifest. Drop into any project that has:
- `infra/docker-compose.yml`
- `services/<name>/.venv` (Python project)
- `.env` + `.env.example`
