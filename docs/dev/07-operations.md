---
title: Sediment Operations
product: sediment
doc_type: operations
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: operators
source_paths:
  - infra/deploy
  - docs/runbooks
  - services/sediment/lab_lib/settings.py
quality_gates:
  - setup-documented
  - failure-paths-documented
  - source-paths-exist
---

# Sediment Operations

## Local Setup

Backend work starts in `services/sediment` with Python tooling and pytest.
Frontend work starts in `frontend` with npm scripts. Operators should keep
database URLs, model provider keys, Discord/GitHub connector secrets, JWT
settings, and Fly credentials out of the repo. `lab_lib/settings.py` is the
configuration boundary; undocumented environment variables should not be added
ad hoc in application code.

## Production Operation

Production is a service topology problem: nginx routes external traffic to
internal FastAPI services, scheduler jobs move external events into memory, and
Postgres/pgvector stores both operational and retrieval state. Monitor service
health, queue/capture freshness, token/auth failures, model latency, cost, and
citation quality. Tenant isolation incidents outrank feature incidents because
they can compromise trust across customers.

## Incident Response

For chat failures, check auth, SSE, retrieval, model provider, and persistence
in that order. For capture failures, check connector credentials, watermarks,
dedupe, distillation, embedding, and insert errors. For deployment failures,
check nginx config, process supervisor state, migrations, and environment
settings. Each incident should end with a test, runbook update, or ADR when the
fix changes architecture or operating policy.
