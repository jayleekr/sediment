---
title: Sediment Release Process
product: sediment
doc_type: release
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: release owners
source_paths:
  - infra/deploy/release.sh
  - infra/deploy/fly.toml
  - services/sediment/pyproject.toml
quality_gates:
  - version-documented
  - rollback-documented
  - source-paths-exist
---

# Sediment Release Process

## Version Source

The product version comes from `services/sediment/pyproject.toml`. The member
portal version is separate and must not be confused with Sediment runtime
version. A release PR that changes behavior should update this docs set, design
docs if architecture changes, and release notes or runbooks if operators need to
act differently.

## Build And Deploy

Deployment assets live in `infra/deploy`. The release process packages backend
services, confirms environment variables, runs migrations or migration checks,
deploys to Fly, and verifies nginx routing to internal services. Frontend
deployment must remain compatible with backend route and auth contracts. The
release owner should record git ref, product version, migration status, smoke
results, and known risk before declaring a release ready for members.

## Rollback

Rollback must account for code, database, and connector watermarks. If only app
code regresses, redeploy the previous image and confirm chat/capture smoke. If a
schema migration regresses, follow the migration rollback plan or freeze writes
until data safety is understood. If connector ingestion regresses, pause the
scheduler before replaying events so duplicate or stale memory is not promoted.
