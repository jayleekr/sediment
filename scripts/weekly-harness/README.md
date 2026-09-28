# weekly-harness

Deterministic validators for the HypeProof weekly operating loop
(`docs/WEEKLY-LOOP.ko.md`). Canonical source lives in `hypeproof-harness`;
vendored into each consumer repo (`hypeproof-studio`, `sediment`,
`hypeprooflab`) via `scripts/sync.sh` with a `HARNESS_VERSION` marker.

All three scripts are stdlib-only Python 3 and talk to GitHub through the `gh`
CLI — no tokens are read or stored.

## check.py — pre-meeting gate

For a cycle label (`weekly-YYYY-MM-DD` — the date of the NEXT Monday
meeting), across the three product repos:

**Open** issues carrying that label must have

- an `ETA: YYYY-MM-DD` line with a date `<=` the cycle date, and
- an `Owner` / `담당` section or line.

**Closed** issues carrying that label, **closed on or after
`2026-07-22T00:00:00+09:00`**, must have (원칙 4 — completion gate)

- an `Evidence: <url>` (or `증거: <url>`) line in the body or in any comment,
  where `<url>` is one of
  - `https://github.com/<owner>/<repo>/pull/<n>`
  - `https://github.com/<owner>/<repo>/commit/<sha>` (7–40 hex)
  - `https://github.com/<owner>/<repo>/issues/<n>#issuecomment-<id>`
- **or** a PR that GitHub itself recorded as closing the issue
  (`closedByPullRequestsReferences`),
- **or** an enumerated exemption — `Evidence-Exemption: cancelled | duplicate |
  administrative | no-deliverable`, or GitHub's "closed as not planned" state.

The cutoff is derived from the issue's `closedAt`, a fact GitHub records.
Issues closed earlier are exempt automatically and **no historical issue is
ever edited to satisfy the gate**. The exemption is deliberately *not* a label:
a label anyone with write access can add is an exemption anyone can grant
themselves. For the same reason `no-evidence-needed` no longer exempts
anything, and free-form exemption text is rejected — only the four codes above.

The ref reuses GitHub's own identifiers rather than a new registry, and needs
*both* the `Evidence:` marker and a permalink shape — a bare link pasted in a
body is ordinary issue chatter and must not satisfy the gate by accident.

Markers only count in **assertive prose**. Fenced code blocks, indented
(4-space) code blocks, inline code spans, blockquotes, and HTML comments are
blanked before matching, for both the evidence gate and the Owner/ETA rule: the
example in WEEKLY-LOOP.ko.md §6.1 is a fenced block, and pasting documentation
must not count as doing the work. The stripper errs toward blanking — a real
marker that got swallowed is fixed by unindenting one line, whereas a swallowed
check is a silent bypass.

### Existence is reported separately from syntax

The checker never fetches a URL, so the two pass states are never conflated:

| state | meaning |
|---|---|
| `github-linked` | GitHub records a PR that closed this issue — existence verified by GitHub |
| `syntax_valid` | the ref is well-formed; `existence_unverified` |

A well-formed ref to a nonexistent PR number still passes as `syntax_valid`.
That is stated in every report rather than hidden.

```bash
python3 scripts/weekly-harness/check.py --cycle weekly-2026-07-21
python3 scripts/weekly-harness/check.py --cycle weekly-2026-07-21 --json
```

Every run ends with a **Coverage — non-vacuity** block (repos/issues/comments
examined, clean accepted, violations detected, each exemption class). Under
`CI` / `GITHUB_ACTIONS` / `HYPEPROOF_ENFORCE`, `--min-issues` defaults to 1, so
a run that examined nothing exits `2` instead of reporting green.

`--skip-evidence-gate` disables only the closed-issue rule, for local triage.
It **exits 2** when `CI`, `GITHUB_ACTIONS`, or `HYPEPROOF_ENFORCE` is set. A
gate that ships with its own bypass wired in is not a gate.

Issue fetching escalates `gh issue list --limit` until a page comes back short,
and raises rather than reporting on a set that may have been truncated — a
fixed cap turns the gate into a fail-open no-op once a repo crosses it.

## Invocation — where this actually runs

| point | what runs | when |
|---|---|---|
| `.github/workflows/repo-governance-live.yml` → `weekly-loop-gate` | the live gate against the previous + upcoming cycle across all three repos; fails the job on any violation, on any bypass, or if it examined 0 issues | daily 03:17 UTC + `workflow_dispatch` |
| `.github/workflows/test.yml` → `repo-governance` | `tests/weekly_loop/` plus a replay of `tests/weekly_loop/fixtures/evidence_corpus.json` whose injected-vs-detected counts must match exactly, plus a check that the bypass is refused | every PR and push to `main` |
| `weekly-loop` skill / a human | ad-hoc, §5 and §7 of WEEKLY-LOOP.ko.md | on demand |

The live audit reports; it does not edit issues. Nothing in this harness
modifies an issue to make the gate pass.

Exit codes: `0` clean · `1` violations (each printed) · `2` config/gh error,
refused bypass, or fewer issues examined than `--min-issues`.

## burndown.py — Monday agenda report

Per repo: closed vs open counts for the cycle label plus a table of every
issue (number, title, owner, state). Markdown on stdout — paste it at the
top of the weekly agenda or pipe it into `scripts/notify/notify.py`.

```bash
python3 scripts/weekly-harness/burndown.py --cycle weekly-2026-07-21
```

Exit codes: `0` report generated · `2` config/gh error.

## announce.py — Tuesday broadcast

The human-facing announcement for a cycle: what each member owns this cycle
(grouped by owner across all repos), what carried over unfinished from last
cycle, and how the tracked milestones (workshop dates) are progressing.
Markdown on stdout — publish it as an Artifact and/or post to Discord.

```bash
python3 scripts/weekly-harness/announce.py --cycle weekly-2026-07-27
```

`--prev-cycle` defaults to the Monday one week earlier (the carry-over
source). `--milestone-repo` limits which repos' milestones are read (default:
same as `--repo`). Exit codes: `0` generated · `2` config/gh error.

## Offline / testing

`--issues-json <path>` feeds gh-shaped issue fixtures
(`{"owner/name": [issue, ...]}`) instead of calling `gh`, so
`tests/weekly_loop/` runs deterministically without network or auth.

## Vendoring

Registered in `sync.sh` `SCRIPTS=()`. Fix here and re-sync — never edit the
vendored copies in consumer repos.
