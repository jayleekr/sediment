---
title: Sediment Testing Requirements
product: sediment
doc_type: testing
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: maintainers
source_paths:
  - services/sediment/tests
  - frontend
  - Makefile
quality_gates:
  - unit-layer-present
  - e2e-layer-present
  - executable-commands
---

# Sediment Testing Requirements

## Test Layers

Unit tests cover pure backend logic: auth parsing, chunking, prompts, cost,
grounding checks, connector normalization, migrations, and rate limits.
Integration tests cover RLS, cross-tenant API behavior, device flow, retrieval,
message persistence, and validator contracts. Frontend e2e coverage should
exercise auth, onboarding, conversation, library, admin, and citation rendering.
Operational smoke covers deployed service health and rollback readiness.

## Commands

```bash
# Backend unit and integration tests
cd services/sediment
uv run pytest

# Focused grounding and tenant safety checks
uv run pytest tests/test_grounding_runtime.py tests/test_rls.py tests/test_cross_tenant_full.py

# Frontend checks
cd ../../frontend
npm install
npm run lint
npm run test

# Docs contract
cd ..
python3 scripts/docs-harness/check.py --min-score 95
```

## Release Gate

The minimum release gate is docs contract, backend pytest, tenant isolation,
grounding runtime checks, frontend build/lint, and deployment smoke. If a
provider outage blocks model-dependent tests, the release note must name the
provider, the skipped test, and the fallback risk. A test that protects tenant
isolation, citation grounding, or data durability cannot be waived silently.
For large retrieval changes, add at least one regression query that proves the
expected citation set still appears. For connector changes, add a fixture-based
test that proves watermark and dedupe behavior before running against a live
integration.
