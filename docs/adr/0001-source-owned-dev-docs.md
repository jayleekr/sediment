# 0001 — Source-owned dev docs

Status: Accepted

## Context

Sediment has substantial design documentation, but the member portal should not
become the place where runtime truth is invented. Architecture, requirements,
tests, and release operations need to live beside the backend/frontend code that
they describe.

## Decision

Sediment keeps canonical developer docs in `docs/dev/*`, ADRs in `docs/adr/*`,
and version metadata in `hypeproof.docs.yaml`. `hypeprooflab` imports selected
documents for member-facing publication. The shared checker from
`hypeproof-harness` validates structure, frontmatter, source paths, version, and
minimum content quality.

## Consequences

Documentation changes are reviewed with code changes. The member portal can
focus on access control, visual quality, and deployment. The cost is more
discipline in this repo, but the benefit is lower architecture drift and clearer
release ownership.
