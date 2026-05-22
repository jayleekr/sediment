# Sediment Chat & Session Benchmark Research

Date: 2026-05-22

## Executive Position

Sediment should not try to become a generic ChatGPT clone. The product only wins if the chat UI behaves like an **evidence workbench for team memory**: every answer must be traceable, every session must be recoverable, and important outcomes from chat must turn into durable organizational artifacts.

The minimum bar for a demo chatbot is: login, type a prompt, stream an answer, show a history list. Sediment's product bar should be higher:

1. **Recoverable work**: conversations, branches, saved responses, exports, search, deletion, and retention controls.
2. **Grounded work**: source selection, inline citations, source side panel, claim-level support, freshness, and fail-closed no-evidence behavior.
3. **Reusable work**: turn useful chat outputs into notes, decisions, actions, project memory, and shareable references.
4. **Governed work**: tenant isolation, role-based access, provenance, audit trail, data controls, and privacy boundaries.

## External Benchmarks

### ChatGPT

OpenAI's Projects are positioned as smart workspaces that group chats, files, instructions, memory, and tools into a long-running effort. The key benchmark is not just "chat history"; it is **project-scoped continuity**: chats can reference other conversations in the same project, files and instructions stay together, and shared projects separate project memory from individual memories. ChatGPT also supports branching chats in projects and single-chat sharing that does not expose the full project context.

Relevant expectations for Sediment:

- Project/workspace containers for recurring work, not a flat chat list.
- Project-scoped memory boundaries, especially for shared or sensitive work.
- Move chats into a project, branch a chat, and share one chat without leaking surrounding context.
- Search past chats inside a project.
- Admin-level controls for sharing, retention, and audit.

Source: OpenAI Projects help, updated 2026-05-21: https://help.openai.com/en/articles/10169521-using-projects-in-chatgpt

OpenAI Memory separates durable saved memories from broader chat-history reference. It also exposes controls: turn history reference on/off, delete memories, prioritize/deprioritize memories, and restore memory versions. The important lesson is that "memory" must be legible and controllable; invisible memory creates mistrust.

Source: OpenAI Memory FAQ: https://help.openai.com/en/articles/8590148-memory-faq

### Claude

Claude's recent chat search/memory capability lets users ask Claude to search previous conversations, while preserving project boundaries. Claude also has Incognito chats that are not saved to chat history and are not used for previous-chat search, with enterprise retention caveats.

Relevant expectations for Sediment:

- Natural-language search over past conversations.
- Explicit boundaries: tenant, project, private/incognito session.
- A visible "this answer used past chat search" affordance, equivalent to Claude surfacing the search as a tool call.

Source: Claude chat search and memory help: https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context

Claude Artifacts establish another important benchmark: when an answer becomes substantial and reusable, it moves out of transient chat into a side panel with versioning, edit/iterate, copy, download, and multiple-artifact management. Sediment does not need full app artifacts immediately, but it should adopt the principle: important outputs should become durable objects.

Source: Claude Artifacts help: https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them

### NotebookLM

NotebookLM's core research UX is source-bound chat: users can include/exclude sources, responses use citations from the notebook's sources, citations jump to the quoted context, responses can be saved as notes, and chat history is retained privately with a delete option.

Relevant expectations for Sediment:

- Source scope controls before asking: all vault, selected source types, selected docs, date range, people, decisions only.
- Citation click should open the exact source context, not just a generic library row.
- "Save to note"/"Promote to decision"/"Create action" should exist as first-class post-answer actions.
- Chat history privacy/deletion should be explicit.

Source: Google NotebookLM chat help: https://support.google.com/notebooklm/answer/16179559

### Microsoft 365 Copilot Chat

Microsoft's enterprise benchmark is source governance. Copilot Chat lets users control which sources are available for a prompt, then review inline citations, hover/open source files in a side pane, and inspect a complete source list.

Relevant expectations for Sediment:

- A source menu in the composer, not only backend routing.
- Inline citation hover and a full source side pane.
- "Open in source app" for connected systems later: GitHub, Discord, Slack, Notion.
- Admin-controlled source availability.

Source: Microsoft source control/review help: https://support.microsoft.com/en-us/topic/control-and-review-sources-of-microsoft-365-copilot-chat-responses-fe762067-15b0-4cb1-b8cf-08cc07b07c5d

### Perplexity

Perplexity is the answer-engine benchmark for citations and research spaces. The constructive warning from public feedback is that citations are not enough: users complain when sources do not support the claim, when files/spaces are ignored, or when custom instructions are applied after retrieval. Sediment should use this as a design constraint: source selection and grounding must be verified, visible, and testable.

Relevant expectations for Sediment:

- Citation precision and citation recall, not just "has citations".
- Pre-retrieval source constraints must affect retrieval, not only final answer style.
- A source debug/provenance panel should explain why a citation was selected.

Sources:

- Perplexity Spaces help: https://www.perplexity.ai/help-center/en/articles/10352961
- "Evaluating Verifiability in Generative Search Engines": https://arxiv.org/abs/2304.09848

## Current Sediment State

The repository already has a meaningful foundation:

- Conversation API checks: create/list/post/get/delete.
- SSE checks: stream start, status event, delta event, citation event, done terminator, assistant persistence, TTFT, full-stream time.
- E2E checks: new conversation, streaming UI, citation cards, multi-turn conversation, mobile viewport, sign-out, no-evidence, freshness citation.
- Grounding checks: citation hard gate, answer grounding, claim-level grounding, no-evidence fail-closed.
- Memory consolidation phase: decisions/actions extracted from chat with conversation provenance and idempotence.
- UI routes: `/sediment`, `/sediment/c/[id]`, `/library`, `/members`, `/admin`, `/onboard`, `/pricing`.

This is stronger than a demo chatbot backend. The weak area is the **product surface**: session management, source controls, visible provenance, saved artifacts, and governance are not yet at the level users expect from serious research/work-memory tools.

## Required Sediment Scope

### P0: Must Exist Before Calling It a Reliable Team-Memory Chat

1. Authenticated production chat path
   - GitHub OAuth completion to Sediment JWT.
   - Member matching failure UX.
   - Authenticated prod smoke with a controlled test account or session-injection harness.

2. Conversation/session basics
   - List conversations with title, last updated, owner, source scope, citation count, stale/fresh badge.
   - Rename, delete, archive, duplicate, and branch conversation.
   - Search conversation titles and message contents.
   - Resume last conversation without losing draft.

3. Composer source controls
   - Source scope selector: all vault, selected artifact types, date range, members, decisions, specific docs.
   - "No web / no external / vault only" explicit mode.
   - Visible freshness state before send.

4. Answer evidence UX
   - Inline citations remain clickable.
   - Right-side source panel with exact quoted passage, artifact metadata, date, author, ingest timestamp, retrieval score, and provenance chain.
   - Claim-level support markers for high-risk answers: supported, weakly supported, unsupported.
   - Deterministic no-evidence empty/error state: do not answer with generic prose.

5. Save/reuse actions
   - Save answer as note.
   - Promote answer/turn to decision.
   - Create follow-up action.
   - Copy citation bundle with refs.
   - Export conversation as Markdown/JSON with citations.

6. Session trust controls
   - Clear chat.
   - Delete conversation.
   - Exclude a conversation from memory consolidation.
   - Show what was extracted into decisions/actions from a chat.
   - Retention policy display.

### P1: Differentiators Worth Building Next

1. Project/space model
   - Group chats, files, source scopes, instructions, and saved notes into a project.
   - Project-only memory boundary.
   - Shared project with `owner`, `editor`, `chat-only`, and `viewer`.

2. Conversation memory search
   - Natural-language search over prior sessions.
   - Search result citations should point to exact past message IDs.
   - UI must say when prior chat context was used.

3. Evidence workbench
   - Source comparison view.
   - "Why these sources?" retrieval debug panel.
   - Citation quality score per answer.
   - Freshness diff: what changed since last answer.

4. Durable artifacts
   - Notes, decision memos, action lists, briefs.
   - Version history for saved artifacts.
   - Backlinks: artifact cited by which conversations; conversation produced which decisions.

5. Collaboration
   - Share single conversation safely.
   - Comment on a saved answer/decision.
   - Mention teammate and hand off a cited excerpt.

### P2: Later, Not Blocking the Core

1. Rich artifact editor/canvas.
2. Voice recording and meeting transcript mode.
3. Multi-agent research plans.
4. External write actions into Slack/Notion/Jira/GitHub.
5. Fine-grained admin analytics and billing dashboard.

## Critical Product Risks

1. "Looks cited" but not actually supported
   - The user will lose trust faster from a wrong cited answer than from an honest no-evidence answer.
   - Required mitigation: claim-level grounding, citation precision/recall, and source panel.

2. Session history exists but is not searchable
   - A left sidebar full of opaque titles does not count as memory.
   - Required mitigation: search, rename, archive, project grouping, and generated summaries.

3. Memory is invisible
   - If Sediment extracts decisions/actions but users cannot inspect or exclude them, it feels unsafe.
   - Required mitigation: memory extraction audit UI.

4. Source selection happens only in the model prompt
   - If users cannot constrain retrieval before answer generation, instructions compete with context and will eventually fail.
   - Required mitigation: source controls must be query parameters enforced by backend retrieval.

5. Prod cannot be tested authentically
   - Public-route E2E is not enough.
   - Required mitigation: automated prod auth harness with a test member, or a signed short-lived test session minted in CI.

## Acceptance Benchmarks

### Functional

- User can sign in on prod, create a conversation, ask a question, receive streamed answer, inspect citations, refresh page, and see the persisted assistant answer.
- User can search for a prior conversation by keyword in title or message body.
- User can branch a conversation and both branches keep independent message history.
- User can delete/archive/export a conversation.

### Evidence Quality

- 100% of factual answers contain at least one valid citation or fail closed.
- 0 invalid citation refs.
- Claim-level support score >= 0.80 for golden questions.
- Latest/freshness questions cite the newest DB artifact by deterministic date ordering.
- Source controls are enforced server-side and visible in provenance.

### UX Quality

- First token visible within 4s on short grounded query.
- No blank loading state longer than 300ms without visible feedback.
- Citation side panel opens within 300ms.
- Mobile chat supports reading answer, opening citations, and sending follow-up without horizontal overflow.
- Keyboard path: focus composer, send, tab through citations, open source, return to composer.

### Session/Governance

- Tenant isolation: cross-tenant conversation read returns 404/empty.
- Shared conversation exposes only intended thread, not project or tenant context.
- Deleting a conversation removes it from future memory consolidation.
- Every saved decision has conversation provenance and source-artifact provenance.

## Proposed Test Matrix

1. Prod auth E2E
   - Login redirect starts.
   - OAuth callback succeeds for controlled test member.
   - JWT exchange stores token.
   - `/conversations` loads authenticated data.

2. Chat persistence E2E
   - Create conversation.
   - Send first query.
   - Observe streaming delta.
   - Observe citation cards.
   - Reload page.
   - Assert assistant message and citations persisted.

3. Session management E2E
   - Rename, archive, delete, branch.
   - Search by title.
   - Search by message body.

4. Source-control E2E
   - Select only `decision` sources.
   - Ask a question that would otherwise retrieve research notes.
   - Assert all citations are decisions.

5. Evidence UX E2E
   - Click inline citation.
   - Assert side panel opens with exact quote, ref, author/date, score, provenance.
   - Copy citation bundle.

6. Memory extraction E2E
   - Ask a decision-making conversation.
   - Promote/extract decision.
   - Assert decision links back to conversation and source artifacts.
   - Exclude conversation from memory and assert it is not extracted.

7. UX rubric gate
   - Score screenshots with `services/sediment/validator/ux_rubric.yaml`.
   - Fail CI if any blocker page has axis < 4 or overall < 8 after visual QA loop.

## Recommended Roadmap

### Sprint 1: Production Chat Trust

- Build authenticated prod E2E harness.
- Add source side panel for citations.
- Add conversation search by title/message.
- Add conversation rename/archive/delete UI.
- Make no-evidence UX visually explicit.

### Sprint 2: Session Memory & Reuse

- Add branch conversation.
- Add save answer as note.
- Add promote to decision/action.
- Add extraction audit UI.
- Add export Markdown/JSON.

### Sprint 3: Source Control & Projectization

- Add source selector to composer.
- Enforce source filters in retrieval API.
- Add project/space model.
- Add project-only memory boundary.
- Add shared single-chat link with leakage tests.

### Sprint 4: Quality Gates

- Wire UX rubric as an automated or semi-automated release gate.
- Add accessibility checks.
- Add visual regression screenshots for desktop/mobile.
- Add citation support trend dashboard.

## Bottom Line

Sediment already has enough backend reliability work to become more than a demo. The next decisive step is productizing that reliability in the UI: users must see what was searched, why it was trusted, where the session went, and what durable memory was created from it. The correct target is not "chatbot with citations"; it is "auditable team-memory workspace with chat as the input surface."
