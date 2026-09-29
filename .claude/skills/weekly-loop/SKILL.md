---
name: weekly-loop
description: Decompose HypeProof weekly meeting notes into GitHub issues with Context/Tasks/Owner/ETA, filed to the right product repo under the weekly-YYYY-MM-DD cycle label. Use this whenever the user provides meeting notes to break down, asks to run the weekly loop, file this week's action items, create cycle issues, or says "주간 루프", "회의록 분해", "이슈로 만들어", or "/weekly-loop" with a notes path. Also use it to run the pre-meeting burndown or the Owner/ETA check for a cycle.
---

# weekly-loop

Turn a Monday meeting-notes file into tracked, deadlined GitHub issues — the
Tuesday "AI decomposition" step of the weekly operating loop. The rules live
in `docs/WEEKLY-LOOP.ko.md`; this skill executes them. The point is not to
file many issues. The point is that every action item becomes an issue with
an Owner and an ETA before the next Monday meeting — unrecorded work does
not exist.

The deterministic validators are:

```bash
python3 scripts/weekly-harness/check.py --cycle weekly-<YYYY-MM-DD>
python3 scripts/weekly-harness/burndown.py --cycle weekly-<YYYY-MM-DD>
```

Run everything from the repo root. Humans set direction: always show the
drafted issues and get confirmation before filing (원칙 3 — 사람은 방향,
AI는 실행).

---

## Default Flow

1. Read the meeting notes.

   The user passes a markdown file path (e.g.
   `/weekly-loop meeting-notes/2026-07-14.md`). Read the whole file before
   drafting anything. If no path was given, ask for one.

2. Compute the cycle label — the date of the NEXT Monday meeting, KST.

   ```bash
   python3 -c "import datetime as d; t=d.datetime.now(d.timezone(d.timedelta(hours=9))).date(); print('weekly-'+(t+d.timedelta(days=(7-t.weekday())%7 or 7)).isoformat())"
   ```

   Run on the decomposition day (typically Tuesday) this yields the coming
   Monday. If the meeting notes are dated, sanity-check the label is the
   Monday after that date; when in doubt, confirm with the user.

3. Extract action items.

   An action item is anything the team agreed someone will do: explicit
   TODOs, "다음 주까지", "~하기로 함", decisions that imply work. Skip pure
   status updates and discussion without a commitment. For each item capture:
   what, why (the surrounding context), who (if named), and any deadline
   mentioned.

4. Map each item to its repo.

   | Item is about | Repo |
   |---|---|
   | 공개 사이트 · 콘텐츠 · 운영 · 멤버/커뮤니티 | `jayleekr/hypeprooflab` |
   | Studio 제품 (VSCodium fork · 워크숍 도구 · Worker) | `jayleekr/hypeproof-studio` |
   | 지식 DB · ingest · SaaS 백엔드/프론트 | `jayleekr/sediment` |
   | 공유 규약 · 스킬 · 프로세스 자체 | `jayleekr/hypeproof-harness` |

   Ambiguous items go to the repo where the deliverable will finally live.
   An item spanning repos becomes one issue per repo.

   Also assign a **theme** — one of the five strategy themes, applied as the
   GitHub label `theme:<id>` at filing time (not retrofitted; it is the only
   link the roadmap roll-up has):

   | Item is about | theme |
   |---|---|
   | 강의 · 트랙레코드 · 워크숍 · 강의 컨텐츠 | `theme:education` |
   | Studio 제품 · 배포 · 워커 · 토큰 · 스킬 | `theme:product` |
   | 비트리 · 인바운드 · 세일즈 · 파트너 퍼널 | `theme:sales` |
   | 헌법 · 정관 · 법률 · IP · 법인화 | `theme:governance` |
   | Sediment · 지식DB · AX 케어 · ARR | `theme:sediment` |

   If an item fits no theme, that is a signal to add a theme (edit
   `roadmap.strategy.json` in the site repo) — ask the user, do not guess.

5. Draft each issue with the four required sections.

   Title: imperative, one line, no cycle date in it. Body template:

   ```markdown
   ## Context

   <why this exists — meeting-notes excerpt/summary, enough that an agent
   can pick it up without asking>

   ## Tasks

   - [ ] <concrete step>
   - [ ] <concrete step>

   ## Owner

   담당: @<github-id>

   ## ETA

   ETA: <YYYY-MM-DD>
   ```

   ETA must be on or before the cycle date. If an item is too big to finish
   by Monday, do not stretch the ETA — narrow the issue to a first
   deliverable (design doc, prototype, findings report) due by Monday and
   note the follow-up in Context. If no owner was named in the meeting,
   ask the user rather than guessing.

6. Dedup against open issues before filing.

   ```bash
   gh issue list --repo <owner/name> --state open --limit 100 --json number,title,labels
   gh issue list --repo <owner/name> --state open --search "<key words from title>" --json number,title,url
   ```

   If an open issue already covers the item, do not file a duplicate —
   comment the new context on the existing issue instead, and ensure it
   carries the cycle label and a valid ETA (add/update if the user agrees).

   An open issue carrying a `wip` label is already claimed by another session
   (WEEKLY-LOOP.ko.md §6.0). Treat it as work in progress: add context as a
   comment, never file a parallel issue and never start executing it. A claim
   whose `claim:` comment is more than 24h old is stale and may be reclaimed —
   say so in a comment before taking it.

7. Show the full plan (repo, title, body, **theme**, dedup verdicts) and get
   the user's confirmation. Apply their edits.

8. Ensure the cycle label exists in every target repo, then file with both the
   cycle label and the `theme:<id>` label.

   ```bash
   gh label list --repo <owner/name> --search "weekly-<YYYY-MM-DD>" \
       --json name --jq '.[].name' | grep -qx "weekly-<YYYY-MM-DD>" \
     || gh label create "weekly-<YYYY-MM-DD>" --repo <owner/name> \
          --description "Weekly cycle due <YYYY-MM-DD>" --color "1D76DB"

   # theme:<id> labels are seeded once per repo (education/product/sales/
   # governance/sediment); create on demand with --force if missing.

   gh issue create --repo <owner/name> \
     --title "<title>" \
     --body-file <drafted-body.md> \
     --label "weekly-<YYYY-MM-DD>" \
     --label "theme:<id>" \
     --assignee <github-id>
   ```

   Write each drafted body to a temp file and pass `--body-file` so
   markdown survives quoting.

9. Verify and report.

   ```bash
   python3 scripts/weekly-harness/check.py --cycle weekly-<YYYY-MM-DD>
   ```

   Fix any violation it reports (edit the issue body via
   `gh issue edit <n> --repo <owner/name> --body-file ...`). Then report to
   the user: every created issue URL grouped by repo, items skipped as
   duplicates (with the existing issue URL), and items that still need an
   owner or a decision.

---

## Burndown mode

When the user asks for the pre-meeting report ("번다운", "burndown",
"이번 주 정리") instead of decomposition:

```bash
python3 scripts/weekly-harness/burndown.py --cycle weekly-<YYYY-MM-DD>
```

Paste the markdown output for the user (it goes at the top of the Monday
agenda). Open issues are carried over (new cycle label + new ETA) or dropped
in the meeting — offer to apply carry-overs with `gh issue edit`.

## Announcement mode

After the issues are filed (and carry-overs settled), produce the weekly
broadcast — what each member owns this cycle, what carried over unfinished,
and how the tracked milestones are progressing:

```bash
python3 scripts/weekly-harness/announce.py --cycle weekly-<YYYY-MM-DD>
```

`--prev-cycle` defaults to one week earlier (the carry-over source). Publish
the markdown as an Artifact (default-private, shareable link) so the team sees
one place with their assignments; optionally post the same content to Discord.

### Milestones

The weekly loop tracks WEEK-scoped work with cycle labels. When the meeting
commits to a dated deliverable beyond one week (a workshop, a launch), create a
GitHub milestone for that date and attach the prep issues, so
`announce.py` can report its progress:

```bash
gh api repos/<owner/name>/milestones -f title="<event>" -f due_on="<YYYY-MM-DD>T00:00:00Z" \
  -f description="<why>" -f state=open
gh issue edit <n> --repo <owner/name> --milestone "<event>"
```

Milestones are for cross-week dated events; cycle labels remain the unit for
the weekly loop itself. Do not put every issue in a milestone — only those
whose deadline is an event, not the coming Monday.

---

## Guardrails

- Never file issues without showing the drafts and getting confirmation.
- Never start execution on an issue carrying someone else's fresh `wip` claim,
  and never leave your own `wip` behind — drop it when the work lands (a merged
  `Closes #<N>` PR releases it implicitly). This is a convention, not a gate:
  nothing in CI checks it (WEEKLY-LOOP.ko.md §6.0, §9.1).
- Never invent an owner — ask.
- Never set an ETA after the cycle date — split the work instead.
- Never close a cycle issue without evidence. Leave `Evidence: <GitHub PR /
  commit / issue-comment permalink>` in the closing comment, or state one of
  the four enumerated exemptions — `Evidence-Exemption: cancelled | duplicate |
  administrative | no-deliverable` — or close it as "not planned" (dropped
  work). Applies to issues closed on or after 2026-07-22 00:00 KST; the
  `no-evidence-needed` label no longer exempts anything. `check.py` enforces
  this daily in `repo-governance live audit` — see WEEKLY-LOOP.ko.md §6.1.
- Never "fix" a violation by editing a historical issue. The gate exempts
  pre-cutoff work from `closedAt`; report violations, do not paper over them.
- Never pass `--skip-evidence-gate` in anything automated. It is refused under
  CI and exists only for local triage.
- One issue closes in one repo; split cross-repo items.
- No secrets in issue bodies; meeting notes may contain internal detail —
  summarize, do not paste tokens/URLs with credentials.
