---
name: curator:kill
description: Graceful shutdown of Ralph + background services. Preserves all state files. NOT for emergency abort — use Ctrl-C for that.
user_invocable: true
triggers:
  - "/curator:kill"
  - "stop ralph"
  - "shutdown curator"
---

## Args

```
/curator:kill [--keep-services]   # default: stop services too
```

## Workflow

```bash
# 1. Signal Ralph to stop on next iter check
echo "STOP — manual /curator:kill at $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  >> products/sediment/harness/ralph/JOURNAL.md

# 2. If a Ralph PID file exists, send SIGTERM (graceful)
if [ -f output/bootstrap/ralph.pid ]; then
  pid=$(cat output/bootstrap/ralph.pid)
  kill -TERM "$pid" 2>/dev/null && echo "sent SIGTERM to Ralph pid=$pid"
fi

# 3. Wait up to 30s for Ralph to exit
for i in {1..30}; do
  pgrep -f "ralph.sh" >/dev/null 2>&1 || break
  sleep 1
done

# 4. Stop services (unless --keep-services)
if [ "${1:-}" != "--keep-services" ]; then
  for pid_f in output/bootstrap/{ingester,metadata,curator_platform,curator_langgraph}.pid; do
    [ -f "$pid_f" ] && kill -TERM "$(cat "$pid_f")" 2>/dev/null
  done
  docker compose -f products/sediment/infra/docker-compose.yml stop 2>&1 | tail -3
fi

echo "shutdown complete. resume with: make ralph-resume"
```

## Hard rules

- Never `docker compose down -v` (destroys volumes).
- Never delete state files (TODO/JOURNAL/STATE).
- Always SIGTERM (not SIGKILL) — Ralph cleans up STATE.json on exit.
