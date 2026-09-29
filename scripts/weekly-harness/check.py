#!/usr/bin/env python3
"""hypeproof weekly-loop checker — validate cycle issues before the meeting.

Canonical source in hypeproof-harness; vendored into each consumer repo
(hypeproof-studio, sediment, hypeprooflab) via sync.sh. Intentionally uses
only the Python standard library so each product repo can vendor and run it
without bootstrapping a package manager. GitHub access goes through the `gh`
CLI — no tokens are read or stored here.

Two rules, one per side of an issue's life:

OPEN issues (docs/WEEKLY-LOOP.ko.md §4) carrying the cycle label
`weekly-YYYY-MM-DD` must have
  - an `ETA: YYYY-MM-DD` line whose date is <= the cycle date, and
  - an `Owner` / `담당` section or line.

CLOSED issues (§2 원칙 4 — "산출물은 증거로 축적돼야 완료") closed on or after
the cutoff (EVIDENCE_CUTOFF, 2026-07-22T00:00:00+09:00) must either
  - reference evidence: an `Evidence: <url>` (or `증거: <url>`) line in the
    issue body or in any comment, where <url> is a GitHub permalink to the
    thing that was produced — a PR, a commit, or an issue comment; or
  - be closed by a PR that GitHub itself recorded
    (`closedByPullRequestsReferences` — GitHub's own link, not typed by a
    human, so it cannot be fabricated in issue text); or
  - carry an explicit, enumerated exemption:
    `Evidence-Exemption: cancelled|duplicate|administrative|no-deliverable`,
    or GitHub's own "closed as not planned" state.

Issues closed BEFORE the cutoff are exempt. That exemption is derived from the
issue's `closedAt` timestamp — a fact GitHub records — and deliberately NOT
from a label anyone can add. History is never edited into compliance.

The evidence ref deliberately reuses GitHub's existing identifiers instead of
inventing a namespace, and deliberately requires both the `Evidence:` marker
and a permalink shape — a bare link pasted in a body does not accidentally
satisfy the gate, and a satisfied gate always points at something a human can
open.

Two pass states are reported and never conflated:
  - `github-linked`  GitHub records a PR that closed the issue → existence
                     verified by GitHub.
  - `syntax_valid`   the ref is well-formed but the checker did not fetch it →
                     existence_unverified.

Markers are only honoured in *assertive* prose. Text inside fenced code
blocks, indented (4-space) code blocks, inline code spans, blockquotes, and
HTML comments is quoted material, not a claim about this issue, and is ignored
for BOTH the evidence gate and the Owner/ETA rule.

USAGE:
    check.py --cycle weekly-2026-07-21
    check.py --cycle weekly-2026-07-21 --repo jayleekr/sediment
    check.py --cycle weekly-2026-07-21 --issues-json fixtures.json   # offline
    check.py --cycle weekly-2026-07-21 --json                        # machine
    check.py --cycle weekly-2026-07-21 --skip-evidence-gate          # local only

`--issues-json` bypasses `gh` for deterministic tests: a JSON object mapping
"owner/name" to a list of gh-shaped issue dicts (number, title, body, url,
state, stateReason, closedAt, labels, comments,
closedByPullRequestsReferences). An issue with no `state` key is treated as
OPEN; a closed issue with no `closedAt` is treated as post-cutoff (fail
closed — an unknown close time is never an exemption).

`--skip-evidence-gate` is refused when CI / GITHUB_ACTIONS / HYPEPROOF_ENFORCE
is set: a gate that ships with its own bypass wired into enforcement is not a
gate.

EXIT CODES:
    0  every cycle issue conforms
    1  violations found (each is printed)
    2  config error / bad cycle label / gh failure / bypass refused /
       fewer issues examined than --min-issues (a control that examined
       nothing has not passed)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPOS = [
    "jayleekr/hypeprooflab",
    "jayleekr/hypeproof-studio",
    "jayleekr/sediment",
]

KST = dt.timezone(dt.timedelta(hours=9))
# Approved policy cutoff: the evidence requirement applies to work closed on or
# after this instant. Derived from closedAt, never from a label.
EVIDENCE_CUTOFF = dt.datetime(2026, 7, 22, 0, 0, 0, tzinfo=KST)

CYCLE_RE = re.compile(r"^weekly-(\d{4})-(\d{2})-(\d{2})$")
# Both bold placements are idiomatic markdown and both get typed by hand:
# '**ETA**: 2026-07-20' and '**ETA:** 2026-07-20'. The trailing (?:\*\*)? after
# the colon is what accepts the second form — without it the value capture
# swallows the '**' and the marker reads as an empty, unactionable value.
BOLD_MARKER_COLON = r"(?:\*\*)?\s*[:：]\s*(?:\*\*)?\s*"
ETA_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?ETA" + BOLD_MARKER_COLON + r"(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
# '## ETA' heading with the date on the first non-empty line below it
# (e.g. issues filed before the inline 'ETA:' template existed)
ETA_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+(?:\*\*)?ETA(?:\*\*)?\s*$\n+\s*(?:\*\*)?(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
OWNER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+(?:\*\*)?(?:Owner|담당자?)(?:\*\*)?\s*$"  # '## Owner' / '## 담당' heading
    r"|(?:[-*]\s*)?(?:\*\*)?(?:Owner|담당자?)(?:\*\*)?\s*[:：])",
    re.IGNORECASE | re.MULTILINE,
)
# 'Evidence: <url>' / '증거: <url>' — same line shape as the ETA marker above.
# 'Evidence-Exemption:' does not match it: '-' is not ':'.
EVIDENCE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:Evidence|증거)" + BOLD_MARKER_COLON + r"(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
# 'Evidence-Exemption: <code>' — one enumerated code and nothing else.
EXEMPTION_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:Evidence-Exemption|증거-면제)"
    + BOLD_MARKER_COLON + r"(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FENCE_RE = re.compile(r"(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
BLOCKQUOTE_RE = re.compile(r"^ {0,3}>")
HTML_COMMENT_1LINE_RE = re.compile(r"<!--.*?-->")
# The ref must be a GitHub permalink to something that was actually produced.
# Reusing GitHub's identifiers keeps the gate verifiable without a new registry.
EVIDENCE_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/"
    r"(?:pull/\d+"                     # PR that carried the work
    r"|commit/[0-9a-f]{7,40}"          # commit that landed it
    r"|issues/\d+\#issuecomment-\d+)$"  # comment holding the deliverable/report
)

# NARROW and closed. Free-form exemption text is rejected on purpose: an
# exemption anyone can phrase is an exemption nobody can audit.
EXEMPTION_CODES = {
    "cancelled": "취소 — 작업이 중단되어 산출물이 없음",
    "duplicate": "중복 — 다른 이슈에서 처리됨",
    "administrative": "행정 — 설정/라벨 등 산출물이 없는 운영 작업",
    "no-deliverable": "산출물 없음 — 논의·결정만 남은 작업",
}
# Retired: this label used to exempt an issue. A label anyone with write access
# can add is not an audit trail, so it no longer exempts anything — it only
# produces a hint inside the violation message.
RETIRED_EXEMPT_LABEL = "no-evidence-needed"

# gh truncates `--limit N` silently. Escalate until a page comes back short;
# refuse to report on a set we know may be partial rather than fail open.
GH_LIMIT_START = 200
GH_LIMIT_MAX = 5000

BYPASS_ENV_VARS = ("CI", "GITHUB_ACTIONS", "HYPEPROOF_ENFORCE")
TRUTHY = {"1", "true", "yes", "on"}


def parse_cycle_date(label: str) -> dt.date:
    m = CYCLE_RE.match(label)
    if not m:
        raise ValueError(
            f"bad cycle label {label!r} — expected weekly-YYYY-MM-DD (date of the NEXT Monday meeting)"
        )
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def enforcement_env() -> str | None:
    """Name of the env var marking this run as an enforcement path, if any."""
    for var in BYPASS_ENV_VARS:
        if os.environ.get(var, "").strip().lower() in TRUTHY:
            return var
    return None


GH_JSON_FIELDS = (
    "number,title,body,url,state,stateReason,closedAt,labels,comments,"
    "closedByPullRequestsReferences"
)


def _gh_issue_page(repo: str, label: str, limit: int) -> list[dict]:
    proc = subprocess.run(
        [
            "gh", "issue", "list",
            "--repo", repo,
            "--label", label,
            "--state", "all",
            "--limit", str(limit),
            "--json", GH_JSON_FIELDS,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"gh issue list failed for {repo}: {detail}")
    return json.loads(proc.stdout or "[]")


def gh_cycle_issues(repo: str, label: str) -> list[dict]:
    """Fetch every cycle issue, or fail — never a silently truncated prefix.

    `gh issue list --limit N` returns at most N and says nothing when it cut
    the list short. Closed issues accumulate monotonically, so a fixed cap
    turns the gate into a fail-open no-op the moment a repo crosses it.
    Escalate the limit until a page comes back short (proof we saw the end);
    if even the ceiling comes back full, raise instead of reporting on a
    partial set.
    """
    limit = GH_LIMIT_START
    while True:
        issues = _gh_issue_page(repo, label, limit)
        if len(issues) < limit:
            return issues
        if limit >= GH_LIMIT_MAX:
            raise RuntimeError(
                f"{repo}: gh returned a full page of {len(issues)} issues at the "
                f"--limit ceiling {GH_LIMIT_MAX} for label {label} — the result may be "
                "truncated; refusing to report on a partial issue set"
            )
        limit = min(limit * 4, GH_LIMIT_MAX)


def strip_quoted(text: str) -> str:
    """Blank out every region where a marker is quoted rather than asserted.

    Covered: fenced code blocks, indented (4-space) code blocks, inline code
    spans, blockquotes, and HTML comments. An `Evidence:` or `ETA:` line inside
    any of them is documentation someone pasted — the example in
    WEEKLY-LOOP.ko.md §6.1 is a fenced block — not a claim about this issue.

    Lines are blanked rather than deleted so the line-anchored markers keep
    their meaning. Deliberately errs toward blanking: a genuine marker that got
    swallowed is fixed by unindenting one line, whereas a swallowed *check* is
    a silent bypass.
    """
    out: list[str] = []
    fence: str | None = None
    fence_len = 0
    in_comment = False
    in_indented = False
    prev_blank = True  # start of document behaves like a blank line

    for raw_line in text.split("\n"):
        line = raw_line.expandtabs(4)

        # --- HTML comments (may span lines) ---
        if in_comment:
            close = line.find("-->")
            if close == -1:
                out.append("")
                prev_blank = False
                continue
            line = line[close + 3:]
            in_comment = False
        line = HTML_COMMENT_1LINE_RE.sub("", line)
        open_at = line.find("<!--")
        if open_at != -1:
            in_comment = True
            line = line[:open_at]

        stripped = line.lstrip()

        # --- fenced code blocks ---
        m = FENCE_RE.match(stripped)
        if fence is not None:
            if m and stripped[0] == fence and len(m.group(1)) >= fence_len:
                fence = None
            out.append("")
            prev_blank = False
            continue
        if m:
            fence = stripped[0]
            fence_len = len(m.group(1))
            out.append("")
            prev_blank = False
            continue

        if not line.strip():
            out.append("")
            prev_blank = True  # blank lines do not end an indented block
            continue

        # --- indented (4-space) code blocks ---
        indent = len(line) - len(line.lstrip(" "))
        if indent >= 4 and (prev_blank or in_indented):
            in_indented = True
            out.append("")
            prev_blank = False
            continue
        in_indented = False
        prev_blank = False

        # --- blockquotes ---
        if BLOCKQUOTE_RE.match(line):
            out.append("")
            continue

        out.append(INLINE_CODE_RE.sub("", line))

    return "\n".join(out)


# Kept as an alias: the vendored docs and consumer notes refer to strip_code.
strip_code = strip_quoted


def check_issue(body: str, cycle_date: dt.date) -> list[str]:
    """Return human-readable violation reasons for one OPEN issue body.

    Quoted regions are stripped first: an `ETA:` inside a fence is the issue
    template someone pasted, not a commitment.
    """
    violations: list[str] = []
    body = strip_quoted(body or "")

    m = ETA_RE.search(body) or ETA_HEADING_RE.search(body)
    if not m:
        violations.append("missing 'ETA:' line")
    else:
        raw = m.group(1).strip().rstrip(".,;)").strip("*")  # tolerate 'ETA: **2026-07-21**'
        if not raw:
            # e.g. a bare 'ETA:' with the date on the next line. Naming the
            # shape beats reporting an unparseable date of ''.
            violations.append("'ETA:' marker has no date after it (want ETA: YYYY-MM-DD)")
        else:
            try:
                eta = dt.date.fromisoformat(raw)
            except ValueError:
                violations.append(f"unparseable ETA date {raw!r} (want YYYY-MM-DD)")
            else:
                if eta > cycle_date:
                    violations.append(
                        f"ETA {eta.isoformat()} is after the cycle date "
                        f"{cycle_date.isoformat()} — split into a first "
                        "deliverable due by Monday"
                    )

    if not OWNER_RE.search(body):
        violations.append("missing 'Owner'/담당 section")

    return violations


def is_open(issue: dict) -> bool:
    """Absent state means open — keeps pre-existing Owner/ETA fixtures valid."""
    return str(issue.get("state") or "OPEN").upper() == "OPEN"


def issue_label_names(issue: dict) -> set[str]:
    names: set[str] = set()
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if name:
            names.add(str(name))
    return names


def parse_closed_at(issue: dict) -> dt.datetime | None:
    """The close instant GitHub recorded, or None if absent/unparseable."""
    raw = str(issue.get("closedAt") or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def linked_pr_urls(issue: dict) -> list[str]:
    """PR URLs GitHub itself recorded as closing this issue.

    Not typed by a human into issue text, so it cannot be fabricated the way an
    `Evidence:` line can — the strongest completion signal available without
    fetching anything.
    """
    urls: list[str] = []
    for ref in issue.get("closedByPullRequestsReferences") or []:
        if isinstance(ref, dict) and ref.get("url"):
            urls.append(str(ref["url"]).rstrip("/"))
        elif isinstance(ref, str):
            urls.append(ref.rstrip("/"))
    return urls


def assertive_texts(issue: dict) -> list[str]:
    """Body plus every comment — evidence normally lands in the closing comment."""
    texts = [issue.get("body") or ""]
    for comment in issue.get("comments") or []:
        if isinstance(comment, dict):
            texts.append(comment.get("body") or "")
        elif isinstance(comment, str):
            texts.append(comment)
    return [strip_quoted(t) for t in texts]


# Backwards-compatible name used by the previous revision and the docs.
evidence_sources = assertive_texts


def comment_count(issue: dict) -> int:
    return len(issue.get("comments") or [])


def check_closed_issue(issue: dict, cutoff: dt.datetime = EVIDENCE_CUTOFF) -> dict:
    """Classify one CLOSED issue against the evidence gate (원칙 4).

    Returns {"status", "detail", "violations"} where status is one of
      exempt_pre_cutoff | exempt_not_planned | exempt_code
      | ok_github_linked | ok_syntax_valid | violation
    """
    closed_at = parse_closed_at(issue)
    if closed_at is not None and closed_at < cutoff:
        return {
            "status": "exempt_pre_cutoff",
            "detail": f"closed {closed_at.astimezone(KST).isoformat()}, before the "
                      f"{cutoff.isoformat()} cutoff",
            "violations": [],
        }

    unknown_close_time = closed_at is None
    texts = assertive_texts(issue)
    linked = linked_pr_urls(issue)

    # 1. A typed Evidence ref, checked for shape.
    malformed: list[str] = []
    empty_marker = False
    for text in texts:
        for m in EVIDENCE_RE.finditer(text):
            raw = m.group(1).strip().rstrip(".,;)").strip("*")
            if not raw:
                # e.g. a bare 'Evidence:' with the URL on the next line. Naming
                # the shape beats reporting a malformed ref of ''.
                empty_marker = True
                continue
            if EVIDENCE_URL_RE.match(raw):
                if raw.rstrip("/") in linked:
                    return {
                        "status": "ok_github_linked",
                        "detail": f"{raw} — matches a PR GitHub recorded as closing this "
                                  "issue (existence verified by GitHub)",
                        "violations": [],
                    }
                return {
                    "status": "ok_syntax_valid",
                    "detail": f"{raw} — syntax_valid, existence_unverified "
                              "(the checker does not fetch the URL)",
                    "violations": [],
                }
            malformed.append(raw)

    # 2. GitHub's own closing-PR record, even with no typed marker.
    if linked:
        return {
            "status": "ok_github_linked",
            "detail": f"closed by {', '.join(linked)} — GitHub-recorded link "
                      "(existence verified by GitHub)",
            "violations": [],
        }

    # 3. An enumerated exemption code. Free-form text is rejected.
    bad_codes: list[str] = []
    for text in texts:
        for m in EXEMPTION_RE.finditer(text):
            code = m.group(1).strip().strip("*`").strip().lower()
            if code in EXEMPTION_CODES:
                return {
                    "status": "exempt_code",
                    "detail": f"Evidence-Exemption: {code} — {EXEMPTION_CODES[code]}",
                    "violations": [],
                }
            bad_codes.append(m.group(1).strip())

    # 4. GitHub's "closed as not planned" state — a state, not a label.
    if str(issue.get("stateReason") or "").upper() == "NOT_PLANNED":
        return {
            "status": "exempt_not_planned",
            "detail": "closed as not planned — cancelled, nothing was produced",
            "violations": [],
        }

    violations: list[str] = []
    allowed = " | ".join(sorted(EXEMPTION_CODES))
    for code in dict.fromkeys(bad_codes):
        violations.append(
            f"unknown Evidence-Exemption code {code!r} — free-form exemptions are "
            f"rejected; allowed codes: {allowed}"
        )
    for raw in dict.fromkeys(malformed):
        violations.append(
            f"malformed Evidence ref {raw!r} — want a GitHub PR, commit, or "
            "issue-comment URL (https://github.com/<owner>/<repo>/pull/<n>)"
        )
    if empty_marker and not malformed:
        violations.append(
            "'Evidence:' marker has no URL after it — put the permalink on the "
            "same line (https://github.com/<owner>/<repo>/pull/<n>)"
        )
    if not violations:
        hint = ""
        if RETIRED_EXEMPT_LABEL in issue_label_names(issue):
            hint = (
                f" — note: the `{RETIRED_EXEMPT_LABEL}` label no longer exempts anything; "
                "state the reason as `Evidence-Exemption: administrative`"
            )
        violations.append(
            "closed with no 'Evidence:' ref (WEEKLY-LOOP §2 원칙 4) — add "
            "`Evidence: <PR/commit/comment URL>` in the body or a comment, or "
            f"`Evidence-Exemption: <{allowed}>` if it produced no deliverable" + hint
        )
    if unknown_close_time:
        violations.append(
            "close timestamp unavailable — the pre-cutoff exemption cannot be "
            "established, so the gate applies (fail closed)"
        )
    return {"status": "violation", "detail": "", "violations": violations}


CLEAN_STATUSES = (
    "exempt_pre_cutoff",
    "exempt_not_planned",
    "exempt_code",
    "ok_github_linked",
    "ok_syntax_valid",
)


def new_counts() -> dict:
    counts = {
        "repos_examined": 0,
        "issues_examined": 0,
        "open_examined": 0,
        "closed_examined": 0,
        "comments_examined": 0,
        "clean_accepted": 0,
        "violations_detected": 0,
        "evidence_gate_skipped": 0,
    }
    counts.update({status: 0 for status in CLEAN_STATUSES})
    return counts


def coverage_lines(counts: dict, cutoff: dt.datetime) -> list[str]:
    """Non-vacuity block. A control that examined nothing has not passed."""
    return [
        "",
        "## Coverage — non-vacuity",
        f"- repos examined:                 {counts['repos_examined']}",
        f"- issues examined:                {counts['issues_examined']} "
        f"(open {counts['open_examined']} · closed {counts['closed_examined']})",
        f"- comment bodies examined:        {counts['comments_examined']}",
        f"- clean cases accepted:           {counts['clean_accepted']}",
        f"- violations detected:            {counts['violations_detected']}",
        f"- closed exempt, pre-cutoff:      {counts['exempt_pre_cutoff']} "
        f"(closed before {cutoff.isoformat()})",
        f"- closed exempt, not planned:     {counts['exempt_not_planned']}",
        f"- closed exempt, declared code:   {counts['exempt_code']}",
        f"- evidence github-linked:         {counts['ok_github_linked']} "
        "(existence verified by GitHub)",
        f"- evidence syntax_valid:          {counts['ok_syntax_valid']} "
        "(existence_unverified)",
        f"- closed issues NOT checked:      {counts['evidence_gate_skipped']} "
        "(--skip-evidence-gate)",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate weekly-cycle issues: open ones carry Owner + ETA <= cycle "
                    "date, closed ones reference evidence or an enumerated exemption."
    )
    parser.add_argument("--cycle", required=True, help="cycle label, e.g. weekly-2026-07-21")
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        metavar="OWNER/NAME",
        help=f"repo to check (repeatable; default: {', '.join(DEFAULT_REPOS)})",
    )
    parser.add_argument(
        "--issues-json",
        metavar="PATH",
        help='offline fixture: JSON {"owner/name": [issue, ...]} instead of gh',
    )
    parser.add_argument(
        "--skip-evidence-gate",
        action="store_true",
        help="do not check closed issues for an Evidence ref "
             "(local triage only; refused under CI/HYPEPROOF_ENFORCE)",
    )
    parser.add_argument(
        "--min-issues",
        type=int,
        default=None,
        metavar="N",
        help="exit 2 unless at least N issues were examined "
             "(default: 1 under CI/enforcement, 0 otherwise)",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit the machine-readable report on stdout instead of the text report",
    )
    args = parser.parse_args(argv)

    enforcing = enforcement_env()
    if args.skip_evidence_gate and enforcing:
        print(
            f"ERROR  --skip-evidence-gate is refused: {enforcing} is set, so this is an "
            "enforcement path. A gate that ships with its own bypass is not a gate.",
            file=sys.stderr,
        )
        return 2

    try:
        cycle_date = parse_cycle_date(args.cycle)
    except ValueError as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return 2

    min_issues = args.min_issues if args.min_issues is not None else (1 if enforcing else 0)
    repos = args.repos or DEFAULT_REPOS

    fixture: dict[str, list[dict]] | None = None
    if args.issues_json:
        try:
            fixture = json.loads(Path(args.issues_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR  cannot read fixture {args.issues_json}: {exc}", file=sys.stderr)
            return 2

    counts = new_counts()
    records: list[dict] = []
    lines: list[str] = [
        f"# weekly-loop check — cycle {args.cycle} (due {cycle_date.isoformat()})",
        f"# evidence cutoff {EVIDENCE_CUTOFF.isoformat()} — issues closed earlier are exempt",
    ]

    for repo in repos:
        try:
            issues = (
                fixture.get(repo, []) if fixture is not None
                else gh_cycle_issues(repo, args.cycle)
            )
        except RuntimeError as exc:
            print(f"ERROR  {exc}", file=sys.stderr)
            return 2

        counts["repos_examined"] += 1
        lines.append(f"\n{repo} — {len(issues)} issue(s) with label {args.cycle}")
        for issue in issues:
            counts["issues_examined"] += 1
            counts["comments_examined"] += comment_count(issue)
            number = issue.get("number", "?")
            ref = f"#{number} {issue.get('title', '(no title)')}"
            record = {
                "repo": repo,
                "number": number,
                "url": issue.get("url", ""),
                "state": "OPEN" if is_open(issue) else "CLOSED",
                "rule": "owner_eta" if is_open(issue) else "evidence",
                "status": "ok",
                "detail": "",
                "violations": [],
            }

            if is_open(issue):
                counts["open_examined"] += 1
                record["violations"] = check_issue(issue.get("body", ""), cycle_date)
                record["status"] = "violation" if record["violations"] else "ok"
            else:
                counts["closed_examined"] += 1
                if args.skip_evidence_gate:
                    counts["evidence_gate_skipped"] += 1
                    record["status"] = "skipped"
                    lines.append(f"  SKIP       {ref} (closed; evidence gate skipped)")
                    records.append(record)
                    continue
                result = check_closed_issue(issue)
                record.update(result)
                if result["status"] in CLEAN_STATUSES:
                    counts[result["status"]] += 1

            if record["violations"]:
                counts["violations_detected"] += 1
                lines.append(f"  VIOLATION  {ref}")
                for reason in record["violations"]:
                    lines.append(f"    - {reason}")
                if issue.get("url"):
                    lines.append(f"    → {issue['url']}")
            else:
                counts["clean_accepted"] += 1
                label = "EXEMPT" if record["status"].startswith("exempt") else "OK"
                suffix = f" — {record['detail']}" if record["detail"] else ""
                lines.append(f"  {label:<10} {ref}{suffix}")
            records.append(record)

    if args.as_json:
        print(json.dumps(
            {
                "cycle": args.cycle,
                "cycle_date": cycle_date.isoformat(),
                "cutoff": EVIDENCE_CUTOFF.isoformat(),
                "repos": repos,
                "counts": counts,
                "issues": records,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print("\n".join(lines))
        print(
            f"\nTotal: {counts['issues_examined']} issue(s) · "
            f"{counts['violations_detected']} violation(s)"
        )
        print("\n".join(coverage_lines(counts, EVIDENCE_CUTOFF)))

    if counts["issues_examined"] < min_issues:
        print(
            f"ERROR  examined {counts['issues_examined']} issue(s) across "
            f"{counts['repos_examined']} repo(s) — below --min-issues {min_issues}. "
            "A control that examined nothing has not passed.",
            file=sys.stderr,
        )
        return 2

    if counts["violations_detected"]:
        print(
            "✗ weekly-loop check failed — open issues need Owner/ETA, "
            "closed issues need an Evidence ref or an enumerated exemption",
            file=sys.stderr,
        )
        return 1
    # Under --json stdout must stay parseable, so the trailer goes to stderr.
    print(
        f"✓ {counts['issues_examined']} issue(s) examined, 0 violation(s) — "
        "open issues have Owner + ETA within the cycle; closed issues have "
        "evidence or a declared exemption",
        file=sys.stderr if args.as_json else sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
