---
name: curator:resurrect
description: Restart any down service. Idempotent. Use when /curator:status shows ✗ on a port.
user_invocable: true
triggers:
  - "/curator:resurrect"
  - "service down"
  - "restart services"
---

## Args

```
/curator:resurrect [service-name|all]
```

Names: `postgres redis ingester metadata platform langgraph web all`

## Workflow

For each requested service:

```bash
case "$svc" in
  postgres)  docker compose -f products/sediment/infra/docker-compose.yml up -d postgres ;;
  redis)     docker compose -f products/sediment/infra/docker-compose.yml up -d redis ;;
  ingester)
    nc -z localhost 11000 || (cd products/sediment/services/sediment && \
      nohup .venv/bin/uvicorn applications.vault_ingester.main:app --port 11000 \
        > /tmp/curator-ingester.log 2>&1 &)
    ;;
  metadata)
    nc -z localhost 12000 || (cd products/sediment/services/sediment && \
      nohup .venv/bin/uvicorn applications.metadata_svc.main:app --port 12000 \
        > /tmp/curator-metadata.log 2>&1 &)
    ;;
  platform)
    nc -z localhost 10100 || (cd products/sediment/services/sediment && \
      nohup .venv/bin/uvicorn applications.sediment_platform.main:app --port 10100 \
        > /tmp/curator-platform.log 2>&1 &)
    ;;
  langgraph)
    nc -z localhost 10020 || (cd products/sediment/services/sediment && \
      nohup .venv/bin/uvicorn applications.sediment_langgraph.main:app --port 10020 \
        > /tmp/curator-langgraph.log 2>&1 &)
    ;;
  web)
    nc -z localhost 3000 || (cd web && nohup npm run dev > /tmp/curator-web.log 2>&1 &)
    ;;
esac
```

Then poll `nc -z localhost <port>` for up to 30s. Report up/down.

## Hard rules

- Never `docker compose down -v` (destroys volumes).
- Never use `kill -9`. Use SIGTERM via `pkill -TERM` or stop via docker compose.
