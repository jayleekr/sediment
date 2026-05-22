---
title: Sediment Requirements
product: sediment
doc_type: requirements
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: maintainers
source_paths:
  - docs/design
  - services/sediment/tests
  - services/sediment/lab_lib
quality_gates:
  - requirement-ids-present
  - acceptance-criteria-present
  - source-paths-exist
---

# Sediment Requirements

## Requirement Index

| ID | Area | Acceptance criteria | Primary paths |
|---|---|---|---|
| REQ-SED-TENANCY | tenant isolation | every request resolves tenant identity; cross-tenant reads fail tests | `lab_lib/auth.py`, `test_rls*.py` |
| REQ-SED-CAPTURE | event capture | connectors normalize events, honor watermarks, deduplicate external ids | `lab_lib/connectors`, `test_github_repo_connector.py` |
| REQ-SED-DISTILL | memory distillation | source events produce citable chunks with preserved provenance | `lab_lib/chunker.py`, `test_distill.py` |
| REQ-SED-RETRIEVAL | grounded chat | answers retrieve evidence and expose citations; no citation is a regression | `sediment_langgraph`, `test_grounding_runtime.py` |
| REQ-SED-COST | cost discipline | model calls are tracked with tenant/request metadata | `lab_lib/cost_tracker.py` |
| REQ-SED-OBS | observability | logs and audit rows include request id and tenant context | `lab_lib/logging.py`, `lab_lib/audit.py` |
| REQ-SED-DEPLOY | single-VM deployment | nginx exposes only intended routes; services bind internal ports | `infra/deploy` |

## Acceptance Policy

Requirements must be measurable. A design statement without an acceptance
criterion belongs in `docs/design`, not in the requirement index. If a
requirement changes data shape, auth behavior, retrieval semantics, or
deployment topology, the PR must update tests and release notes. Acceptance can
be a unit test, integration test, contract test, or operational smoke command,
but it must name the verification path.

## Drift Policy

Sediment already has rich design docs. The risk is not lack of writing; the
risk is scattered truth. This page is the compact requirement map that tells
maintainers which behavior is stable enough to gate. When a design doc becomes
implementation reality, promote its stable requirement here.
