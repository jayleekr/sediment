---
title: Sediment Directory Structure
product: sediment
doc_type: directory
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: maintainers
source_paths:
  - services
  - frontend
  - infra
  - docs/design
quality_gates:
  - directory-tree-present
  - ownership-boundaries
  - source-paths-exist
---

# Sediment Directory Structure

## Tree

```text
sediment/
├── services/sediment/
│   ├── applications/                 # FastAPI services and MCP server
│   ├── lab_lib/                      # shared auth, DB, RAG, connector libs
│   ├── prompts/                      # distillation and chat prompt assets
│   └── tests/                        # backend unit and contract tests
├── services/sediment-cli/            # CLI tooling
├── services/sediment-mcp/            # MCP package metadata
├── frontend/app/sediment/            # Next.js tenant UI
├── infra/deploy/                     # Docker, Fly, nginx, supervisord
├── infra/migrations/                 # database migration materials
├── docs/design/                      # detailed design documents
├── docs/dev/                         # enforced developer docs contract
└── docs/adr/                         # architecture decision records
```

## Ownership Boundaries

Platform REST concerns belong in `sediment_platform`. Chat and retrieval graph
concerns belong in `sediment_langgraph`. Generic tenant, DB, auth, connector,
chunking, and grounding logic belongs in `lab_lib`. Frontend components must not
reimplement auth or retrieval rules that belong to backend services. Deployment
changes under `infra/deploy` must be reviewed with release and operations docs
because process topology, ports, and reverse proxy rules are part of runtime
architecture.

## Change Policy

A PR that touches `lab_lib/auth.py`, tenant middleware, RLS tests, or frontend
auth pages must update auth requirements. A PR that touches connectors,
chunking, embeddings, retrieval, grounding, or chat composition must update
runtime flow and testing docs. A PR that changes nginx, Fly, supervisord, or
service ports must update release and operations docs before merge.
