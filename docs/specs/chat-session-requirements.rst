Sediment Chat & Session Requirements
====================================

:Date: 2026-05-22
:Status: Draft for Epic #39
:Research baseline: ``docs/research/chat-session-benchmark-2026-05.md``

Purpose
-------

This document turns the chat/session benchmark research into implementable
requirements. It is the reference for product scope, API design, UI design,
QA, and PR review for Epic #39.

Sediment is not scoped as a generic chatbot. The target is an auditable
team-memory workspace where chat is the input surface, citations are
inspectable, and useful conversation outcomes become durable organizational
memory.

Goals
-----

* Make production chat trustworthy end to end: authenticated login, persisted
  sessions, streamed answers, citations, reload recovery.
* Make evidence visible: users can inspect what was searched, why a citation
  was used, and whether claims are supported.
* Make sessions recoverable: users can find, resume, rename, archive, branch,
  delete, and export prior work.
* Make useful answers reusable: users can save notes, promote decisions or
  actions, and copy/export citation bundles.
* Make memory governed: users can see and control what chat content is
  consolidated into durable memory.
* Make quality measurable: each core workflow has acceptance tests and UX
  quality criteria.

Non-Goals
---------

* Do not build a generic consumer chatbot surface unrelated to
  HypeProof/Sediment memory workflows.
* Do not build a full Claude-style artifact app builder in P0.
* Do not add external write actions to Slack, Notion, Jira, or GitHub in P0.
* Do not rely on prompt-only source constraints when a server-side retrieval
  filter is required.
* Do not claim production chat is validated until OAuth completion and
  authenticated conversation flow are covered.

Personas
--------

P-A: Lab Member
  Uses Sediment to ask what the team knows, resume old research threads, and
  save useful answers into notes or decisions.

P-B: Team Lead
  Uses Sediment to inspect decisions, verify provenance, hand off cited
  context, and understand what memory was created from team conversations.

P-C: Admin / Operator
  Uses Sediment to manage source availability, tenant access, auth, freshness,
  retention, quality gates, and production validation.

Core Journeys
-------------

J-01: Ask and Verify
  User signs in on production, creates or resumes a conversation, selects a
  source scope, asks a grounded question, receives a streamed answer, opens
  citations, verifies exact source context, then copies or exports the answer
  with citation bundle.

J-02: Recover Prior Work
  User searches prior conversations by title or message content, opens a
  matching conversation, sees the previous answer, citations, source scope,
  and freshness state, then continues or branches it.

J-03: Convert Chat into Memory
  User saves an answer as a note or promotes it to a decision/action. Sediment
  records conversation provenance and source-artifact provenance. User can
  inspect or exclude the conversation from memory consolidation.

J-04: Govern and Debug Trust
  Admin reviews source freshness, retrieval quality, production auth/chat E2E,
  UX screenshots, grounding metrics, tenant isolation, and retention behavior.

Requirement Levels
------------------

P0
  Required before Sediment can be described as reliable team-memory chat.

P1
  Differentiating workspace capabilities that deepen the product.

P2
  Later expansion after P0/P1 trust surfaces are stable.

P0 Requirements
---------------

REQ-AUTH-001: Production OAuth Chat Path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Production must support a complete authenticated chat path from GitHub OAuth
through Sediment JWT exchange to conversation APIs.

Acceptance criteria:

* GitHub sign-in button starts OAuth from ``/sediment``.
* OAuth callback exchanges GitHub identity for a Sediment JWT.
* Member matching failure renders a clear error and recovery path.
* Authenticated user can load ``/api/v1/conversations``.
* Authenticated user can create a conversation and post a message.
* Production E2E covers this path using a controlled test member or safe
  session-injection harness.

Tests:

* ``E2E-PROD-AUTH-01``: login redirect starts.
* ``E2E-PROD-AUTH-02``: OAuth callback/JWT exchange succeeds for test member.
* ``E2E-PROD-CHAT-01``: authenticated conversation list loads.

REQ-CONV-001: Conversation List Metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Conversation lists must show enough metadata for users to recover prior work.

Acceptance criteria:

* Each row/card shows title, owner/member, last updated time, message count,
  citation count, source scope summary, and freshness state.
* Empty state explains how to start the first conversation.
* Stale/fresh status is visually distinct and accessible.
* Loading state is not blank.

Tests:

* ``E2E-CONV-LIST-01``: list renders metadata for seeded conversations.
* ``UX-CONV-LIST-01``: no blank loading state longer than 300ms.

REQ-CONV-002: Conversation Management Actions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to manage sessions without database or admin intervention.

Acceptance criteria:

* Rename conversation.
* Archive and unarchive conversation.
* Delete conversation with confirmation.
* Duplicate or branch conversation.
* Resume last active conversation.
* Draft input survives navigation within the same browser session.

Tests:

* ``E2E-CONV-MGMT-01``: rename persists after reload.
* ``E2E-CONV-MGMT-02``: archive hides from default list and can be restored.
* ``E2E-CONV-MGMT-03``: delete removes conversation and blocks direct URL access.
* ``E2E-CONV-MGMT-04``: branch creates independent message history.

REQ-CONV-003: Conversation Search
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to search prior work by conversation title and message body.

Acceptance criteria:

* Search matches title text.
* Search matches user and assistant message text.
* Results show matching snippet, conversation title, timestamp, and citation
  count.
* Results are tenant-scoped.
* Empty result state is explicit and actionable.

Tests:

* ``E2E-CONV-SEARCH-01``: title query returns expected conversation.
* ``E2E-CONV-SEARCH-02``: message-body query returns expected conversation.
* ``L2-CONV-SEARCH-03``: cross-tenant search leakage is zero.

REQ-SOURCE-001: Composer Source Controls
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to constrain retrieval before sending a prompt.

Acceptance criteria:

* Composer exposes source scope controls for artifact type, date range,
  members, decisions, specific documents, and all-vault mode.
* "Vault only / no external sources" mode is explicit.
* Selected source scope is sent as structured API input, not only prompt text.
* Backend retrieval enforces selected scope.
* Answer provenance displays the applied source scope.

Tests:

* ``E2E-SOURCE-001``: selecting only ``decision`` sources yields only decision
  citations.
* ``L2-SOURCE-002``: retrieval API enforces artifact type filter.
* ``L2-SOURCE-003``: retrieval API enforces date range filter.

REQ-SOURCE-002: Freshness Visibility
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must see whether the vault/source data is fresh before trusting an
answer.

Acceptance criteria:

* Composer or header shows freshness state before send.
* Stale state includes last ingest timestamp and reason when available.
* Freshness questions use deterministic metadata/date ordering.
* Freshness answer cites the newest relevant artifact.

Tests:

* ``E2E-FRESHNESS-001``: stale badge renders when freshness check reports stale.
* Existing ``P2-E2E-15``: freshness answer carries citation.
* Existing freshness contract tests remain passing.

REQ-EVID-001: Citation Source Panel
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Citations must open an inspectable source panel with exact context.

Acceptance criteria:

* Inline citation click opens a side panel without navigating away.
* Panel shows exact quoted passage or chunk content.
* Panel shows ref, artifact type, date, author/member, ingest timestamp,
  retrieval score, and provenance chain when available.
* Panel supports open-in-library and copy citation.
* Missing provenance is visibly flagged.

Tests:

* ``E2E-EVID-001``: clicking citation opens panel.
* ``E2E-EVID-002``: panel includes exact quote/ref/date/provenance.
* ``E2E-EVID-003``: copy citation bundle includes refs.
* ``UX-EVID-004``: panel opens within 300ms.

REQ-EVID-002: Claim-Level Support Visibility
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

For high-risk answers, users must see whether claims are supported.

Acceptance criteria:

* Claim support states are represented as ``supported``, ``weakly_supported``,
  or ``unsupported``.
* Unsupported claims are not visually buried.
* If claim validation fails, answer shows a trust warning.
* Claim support data is included in persisted assistant message metadata when
  available.

Tests:

* Existing ``P2-GROUND-03``: claim-level grounding contract.
* ``E2E-EVID-CLAIM-01``: unsupported claim warning is visible on seeded
  response.

REQ-EVID-003: No-Evidence Fail-Closed UX
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

When retrieval cannot support an answer, Sediment must fail closed.

Acceptance criteria:

* Assistant does not produce a generic answer when citations are absent or
  invalid.
* UI shows a clear no-evidence state.
* User can refine query or adjust source scope from the no-evidence state.
* No-evidence state is persisted as a response status, not confused with a
  transport error.

Tests:

* Existing ``P2-GROUND-04``: no-evidence fail-closed contract.
* Existing ``P2-E2E-14``: no-evidence chat fails closed.
* ``UX-NOEVID-001``: no-evidence state has clear visual hierarchy.

REQ-REUSE-001: Save Answer as Note
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to save useful assistant output as a durable note.

Acceptance criteria:

* Assistant answer has "Save as note" action.
* Saved note includes answer text, citation bundle, conversation ID, message
  ID, author, and timestamp.
* Saved note appears in a durable notes/artifacts area or library filter.
* Saved note can be reopened from its source conversation.

Tests:

* ``E2E-REUSE-001``: save answer as note and reopen it.
* ``L2-REUSE-002``: note record includes conversation/message provenance.

REQ-REUSE-002: Promote to Decision or Action
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to promote chat outcomes into durable decisions/actions.

Acceptance criteria:

* Assistant answer and selected user/assistant turns can be promoted to
  decision.
* Assistant answer and selected user/assistant turns can be promoted to action.
* Decision/action includes conversation provenance and source-artifact
  provenance.
* Existing consolidation remains idempotent and does not duplicate promoted
  objects.

Tests:

* Existing ``P4-CONSOLIDATE-01``: decisions have conversation provenance.
* Existing ``P4-PROV-01``: decision provenance schema.
* ``E2E-REUSE-DECISION-01``: promote answer to decision and inspect provenance.

REQ-REUSE-003: Export Conversation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to export a conversation for review or backup.

Acceptance criteria:

* Export Markdown includes messages, citations, source scope, timestamps, and
  provenance links.
* Export JSON includes stable IDs for conversation, messages, citations,
  notes/decisions/actions.
* Export respects tenant permissions.
* Deleted conversations cannot be exported.

Tests:

* ``E2E-EXPORT-001``: export Markdown includes citation refs.
* ``L2-EXPORT-002``: JSON export schema validates.
* ``L6-EXPORT-003``: unauthorized export returns 404/403.

REQ-MEM-001: Memory Extraction Audit
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Users must be able to inspect what a conversation contributed to durable
memory.

Acceptance criteria:

* Conversation detail page shows extracted decisions/actions/notes.
* Each extracted object links back to source conversation/message.
* User can exclude a conversation from future memory consolidation.
* Exclusion is enforced by consolidation worker.
* Exclusion state is visible and reversible by authorized users.

Tests:

* ``E2E-MEM-001``: extracted decision appears in conversation audit.
* ``E2E-MEM-002``: exclude conversation prevents future extraction.
* Existing ``P4-CONSOLIDATE-02``: consolidation remains idempotent.

REQ-GOV-001: Tenant and Sharing Boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Chat/session features must preserve tenant isolation and not leak
project/session context.

Acceptance criteria:

* Conversation reads are tenant-scoped.
* Search results are tenant-scoped.
* Shared single-conversation view exposes only intended conversation content.
* Source panel never fetches cross-tenant artifact data.
* Deleted/archived access rules are enforced server-side.

Tests:

* Existing ``P2-E2E-08``: cross-tenant negative test.
* Existing RLS conversation checks remain passing.
* ``L6-GOV-SEARCH-001``: cross-tenant search returns no results.
* ``L6-GOV-SHARE-001``: shared conversation cannot access surrounding
  project/tenant context.

REQ-UX-001: Chat UX Quality Gate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P0

Core chat/session screens must satisfy a measurable UX quality bar.

Acceptance criteria:

* Screens pass ``services/sediment/validator/ux_rubric.yaml`` with overall >= 8
  and no axis < 4.
* No desktop/mobile horizontal overflow in core chat flows.
* Keyboard navigation covers composer, send, citation open, source panel close,
  and return to composer.
* Touch targets are at least 44x44px for primary actions.
* Streaming feedback appears within 100ms of send.

Tests:

* ``UX-CHAT-001``: screenshot rubric score gate.
* ``E2E-MOBILE-CHAT-001``: mobile chat no overflow.
* ``E2E-A11Y-CHAT-001``: keyboard path works.

P1 Requirements
---------------

REQ-PROJ-001: Project/Space Model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P1

Sediment should group conversations, saved notes, source scopes, instructions,
and durable artifacts into project-like spaces.

Acceptance criteria:

* Project has title, description, owner, members, instructions, source scope
  defaults.
* Conversation can be created inside a project.
* Existing conversation can be moved into a project.
* Project can be shared with ``owner``, ``editor``, ``chat_only``, and
  ``viewer``.
* Project-only memory boundary is enforced.

REQ-MEM-SEARCH-001: Natural-Language Past Session Search
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P1

Users should be able to ask Sediment to search prior sessions as a retrieval
source.

Acceptance criteria:

* Past-chat search is explicit and visible as a tool/source event.
* Results cite exact message IDs.
* Project/tenant/private boundaries are enforced.
* User can disable past-chat search for a session.

REQ-WORKBENCH-001: Evidence Workbench
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P1

Users should be able to debug and compare evidence.

Acceptance criteria:

* Source comparison view shows competing citations.
* "Why these sources?" panel explains retrieval arms and scores.
* Citation quality score appears per answer.
* Freshness diff shows what changed since prior similar answer.

REQ-ARTIFACT-001: Durable Artifact Versioning
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P1

Saved notes, decision memos, action lists, and briefs should support version
history and backlinks.

Acceptance criteria:

* Durable artifact has versions.
* Artifact shows source conversation and citations.
* Conversation shows artifacts it produced.
* Artifact can be exported.

REQ-COLLAB-001: Collaboration and Handoff
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:Priority: P1

Users should be able to collaborate around cited outputs.

Acceptance criteria:

* Share a single conversation safely.
* Comment on saved answer/decision.
* Mention teammate on cited excerpt.
* Handoff event is tracked.

P2 Requirements
---------------

* ``REQ-RICH-ARTIFACT-001``: Rich artifact/canvas editor.
* ``REQ-VOICE-001``: Voice recording and transcript-to-memory workflow.
* ``REQ-AGENT-001``: Multi-agent research plan workflow.
* ``REQ-WRITE-001``: External write actions into Slack, Notion, Jira, and GitHub
  with confirmation.
* ``REQ-ADMIN-ANALYTICS-001``: Fine-grained admin analytics and billing
  dashboard.

Data Model Implications
-----------------------

These requirements imply the following durable concepts. Names are provisional.

* ``conversations``: add ``archived_at``, ``deleted_at``, ``source_scope``,
  ``summary``, ``exclude_from_memory``, ``branched_from_conversation_id``.
* ``messages``: add ``support_metadata``, ``source_scope``, ``exportable``,
  ``status``.
* ``conversation_search_index``: title/body/snippet index or materialized
  search view.
* ``saved_notes``: answer text, citation bundle, conversation/message
  provenance.
* ``actions``: promoted or extracted task-like outcomes with provenance.
* ``decisions``: already present concept; ensure promoted decisions and
  consolidated decisions share provenance schema.
* ``projects``: title, description, owner, default source scope, instructions,
  sharing mode.
* ``project_members``: project-level role mapping.
* ``conversation_exports``: optional audit trail for exports.
* ``memory_exclusions``: explicit audit trail for excluded
  conversations/messages.

API Requirements
----------------

Required or extended API surfaces:

* ``GET /api/v1/conversations?query=&archived=&project_id=``
* ``POST /api/v1/conversations``
* ``PATCH /api/v1/conversations/{id}`` for title/archive/exclude/source scope.
* ``DELETE /api/v1/conversations/{id}``
* ``POST /api/v1/conversations/{id}/branch``
* ``GET /api/v1/conversations/{id}/export?format=markdown|json``
* ``GET /api/v1/conversations/search?q=``
* ``POST /api/v1/conversations/{id}/notes``
* ``POST /api/v1/conversations/{id}/decisions``
* ``POST /api/v1/conversations/{id}/actions``
* ``GET /api/v1/conversations/{id}/memory-audit``
* ``POST /v1/sediment/stream`` accepts structured ``source_scope``.
* ``GET /api/v1/library/{ref}`` returns exact citation context where possible.

All APIs must enforce tenant scope server-side.

UI Requirements
---------------

``/sediment``
  Recent conversations list with metadata, search box, archive filter, new
  conversation action, and freshness state.

``/sediment/c/[id]``
  Conversation title rename, branch/archive/delete/export actions, message
  list with persisted citations, composer source controls, streaming status,
  no-evidence state, citation source panel, save/promote/copy actions, and
  memory extraction audit section.

``/sediment/library``
  Exact citation context view, backlinks to conversations and durable
  artifacts, and filters that match source-scope controls.

``/sediment/admin``
  Production E2E/auth validation status, source freshness and connector state,
  UX/grounding quality gate status, retention and memory consolidation
  settings.

Quality Gates
-------------

The following gates should become release checks as implementation lands:

* P0 functional E2E passes on dev.
* Production public smoke passes.
* Production authenticated chat smoke passes.
* Citation hard gate passes.
* Claim-level grounding score >= 0.80 on golden questions.
* No-evidence fail-closed passes.
* Cross-tenant leakage tests pass.
* UX rubric overall >= 8 and no axis < 4 on key screenshots.
* ``npm run build`` passes.
* Backend validator subset for chat/session passes.

Requirement-to-Epic Mapping
---------------------------

Epic #39 maps to this document:

* Prod chat trust: ``REQ-AUTH-001``, ``REQ-SOURCE-002``.
* Session basics: ``REQ-CONV-001``, ``REQ-CONV-002``, ``REQ-CONV-003``.
* Evidence UX: ``REQ-EVID-001``, ``REQ-EVID-002``, ``REQ-EVID-003``.
* Reuse/memory: ``REQ-REUSE-001``, ``REQ-REUSE-002``,
  ``REQ-REUSE-003``, ``REQ-MEM-001``.
* Governance: ``REQ-GOV-001``.
* UX quality: ``REQ-UX-001``.
* Workspace expansion: ``REQ-PROJ-001``, ``REQ-MEM-SEARCH-001``,
  ``REQ-WORKBENCH-001``, ``REQ-ARTIFACT-001``, ``REQ-COLLAB-001``.

Open Questions
--------------

1. What is the safest production auth E2E strategy: controlled test GitHub
   account, signed CI-only session token, or backend test-member OAuth bypass
   restricted to deploy workflow?
2. Should branch/duplicate semantics copy all messages and citations, or create
   a pointer to parent plus divergent suffix?
3. Should saved notes live in the existing library/artifact model or a separate
   note table?
4. How strict should claim-level support be for informal brainstorming answers?
5. What retention policy should apply to deleted conversations versus exported
   conversations?
6. Are projects tenant-wide by default, or private until explicitly shared?
7. Should source scope be persisted per conversation, per message, or both?

Review Rule
-----------

Any PR under Epic #39 should reference at least one requirement ID from this
document and include the matching validation evidence. If a PR changes behavior
without a requirement ID, update this spec first or explicitly state why the
change is outside Epic #39.
