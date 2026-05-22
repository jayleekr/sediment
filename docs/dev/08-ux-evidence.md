---
title: Sediment UX Evidence
product: sediment
doc_type: ux-evidence
status: canonical
owner: core
version: 0.1.0
last_reviewed: 2026-05-22
audience: product reviewers
source_paths:
  - frontend/app/sediment
  - docs/demo
quality_gates:
  - screenshots-present
  - scenario-coverage
  - source-paths-exist
---

# Sediment UX Evidence

## Evidence Set

Sediment evidence should show actual product states: onboarding, auth/device
flow, conversation with citations, library browsing, admin/member management,
freshness indicators, and error states. The target screenshot names are
`docs/evidence/sediment-onboarding.png`, `docs/evidence/sediment-chat-cited.png`,
and `docs/evidence/sediment-library.gif`. These should be produced from
Playwright or a documented manual capture flow, then promoted into member docs
only when they reflect the released version.

## Capture Standard

Every screenshot or GIF needs product version, tenant or fixture name, date,
capture command, and known limitations. Do not use dark cropped images that hide
the citation panel or route state. Evidence for grounded chat must show both the
answer and citations. Evidence for admin must avoid leaking real member data.
When using seeded fixtures, record the fixture path and query text.

## Review Use

Product review should start from the UX evidence before reading implementation
docs. If the UI does not make tenant identity, citation grounding, freshness, or
error recovery visible, the underlying feature is not review-ready. Missing
evidence is acceptable during early implementation only if the release note
explicitly calls it out and a follow-up issue owns the capture.
