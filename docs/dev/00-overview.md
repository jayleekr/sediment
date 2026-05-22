---
title: Sediment Developer Overview
product: sediment
doc_type: overview
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: maintainers
source_paths:
  - services/sediment
  - frontend/app/sediment
  - docs/design
quality_gates:
  - source-paths-exist
  - version-matches-pyproject
  - member-docs-export
---

# Sediment Developer Overview

## Purpose

Sediment is the HypeProof memory and evidence engine for organizations that do
not yet have a reliable knowledge layer. It captures events from channels such
as Discord and GitHub, distills them into citable memory, stores text and vector
representations, and serves grounded chat through a tenant-aware web interface.
The repo contains backend services, shared libraries, a Next.js frontend, infra
deployment files, design docs, and validation tests. The business claim is that
small organizations can ask questions against their own operating memory and get
answers with citations rather than ungrounded summaries.

## Repository Scope

This repo owns Sediment runtime behavior: FastAPI services, retrieval and chat,
tenant auth, connector ingestion, distillation, vector/BM25 retrieval, frontend
routes, deployment assets, and validator tests. It does not own Studio desktop
behavior or the member docs portal. `hypeprooflab` may render selected Sediment
docs, but the source of truth for architecture, testing, release, and operations
stays here.

## Maintainer Reading Order

Start with `docs/design/01-architecture-overview.md` for the existing deep
architecture narrative, then use this `docs/dev` set as the enforced maintainer
contract. Read `01-architecture.md`, `03-runtime-flows.md`, and
`05-testing-requirements.md` before changing backend services. Read
`06-release-process.md` and `07-operations.md` before deployment work.
