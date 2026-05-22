---
title: Sediment Runtime Flows
product: sediment
doc_type: runtime
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: maintainers
source_paths:
  - services/sediment/applications/sediment_langgraph/main.py
  - services/sediment/applications/vault_ingester/main.py
  - services/sediment/lab_lib/connectors
quality_gates:
  - runtime-flows-present
  - failure-paths-documented
  - source-paths-exist
---

# Sediment Runtime Flows

## Capture Flow

The scheduler invokes connector jobs for configured tenant integrations. A
connector reads from its source using a stored watermark, normalizes external
events, and passes accepted events to ingest. The ingester deduplicates,
distills, chunks, embeds, and writes durable rows to Postgres/pgvector. Failure
signals include connector API errors, stale watermarks, duplicate spikes,
embedding failures, cost overrun, and missing tenant context. A failed connector
must not corrupt another tenant's state.

## Chat Flow

A member opens a conversation in the frontend. The frontend persists the user
turn through platform REST and opens SSE against the LangGraph service. Backend
auth resolves tenant identity, retrieval combines text and vector evidence,
composition produces a cited answer, and the service persists the assistant turn
before completion. The UI renders deltas, citations, freshness state, and
errors. No citation should be treated as a regression for grounded-answer paths.

## Admin And Feedback Flow

Admin routes manage members, issuer/token flows, library state, billing,
signals, and promote-to-golden actions. Feedback routes capture quality signals
for later evaluation. These paths must keep tenant identity explicit and must
leave enough audit detail to debug who changed memory, membership, or validation
state. Observability should include request id, tenant id, route, latency, model
choice, cost, and grounding outcome where available.
