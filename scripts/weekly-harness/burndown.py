#!/usr/bin/env python3
"""hypeproof weekly-loop burndown — pre-meeting report for a cycle label.

Canonical source in hypeproof-harness; vendored into each consumer repo
(hypeproof-studio, sediment, hypeprooflab) via sync.sh. Intentionally uses
only the Python standard library so each product repo can vendor and run it
without bootstrapping a package manager. GitHub access goes through the `gh`
CLI — no tokens are read or stored here.

Per repo it reports closed vs open counts for the cycle label and lists each
issue (number, title, owner, state). Output is markdown, suitable for pasting
at the top of the Monday agenda (docs/WEEKLY-LOOP.ko.md §7). The Monday
meeting opens with this table.

USAGE:
    burndown.py --cycle weekly-2026-07-21
    burndown.py --cycle weekly-2026-07-21 --repo jayleekr/sediment
    burndown.py --cycle weekly-2026-07-21 --issues-json fixtures.json  # offline

`--issues-json` bypasses `gh` for deterministic tests: a JSON object mapping
"owner/name" to a list of gh-shaped issue dicts (number, title, body, state,
assignees, url).

EXIT CODES:
    0  report generated (open issues are the meeting's business, not a failure)
    2  config error / bad cycle label / gh failure
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPOS = [
    "jayleekr/hypeprooflab",
    "jayleekr/hypeproof-studio",
    "jayleekr/sediment",
]

CYCLE_RE = re.compile(r"^weekly-\d{4}-\d{2}-\d{2}$")
OWNER_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:Owner|담당자?)(?:\*\*)?\s*[:：]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def gh_cycle_issues(repo: str, label: str) -> list[dict]:
    proc = subprocess.run(
        [
            "gh", "issue", "list",
            "--repo", repo,
            "--label", label,
            "--state", "all",
            "--limit", "200",
            "--json", "number,title,body,state,assignees,url",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"gh issue list failed for {repo}: {detail}")
    return json.loads(proc.stdout or "[]")


def issue_owner(issue: dict) -> str:
    """Owner line from the body wins; fall back to the first assignee."""
    m = OWNER_LINE_RE.search(issue.get("body") or "")
    if m:
        return m.group(1).strip()
    assignees = issue.get("assignees") or []
    if assignees:
        login = assignees[0].get("login") if isinstance(assignees[0], dict) else assignees[0]
        if login:
            return f"@{login}"
    return "—"


def render(cycle: str, repos: list[str], issues_by_repo: dict[str, list[dict]]) -> str:
    lines: list[str] = [f"# Weekly burndown — {cycle}", ""]
    grand_open = 0
    grand_closed = 0
    for repo in repos:
        issues = issues_by_repo.get(repo, [])
        open_issues = [i for i in issues if str(i.get("state", "")).upper() == "OPEN"]
        closed_issues = [i for i in issues if str(i.get("state", "")).upper() != "OPEN"]
        grand_open += len(open_issues)
        grand_closed += len(closed_issues)
        lines.append(f"## {repo} — closed {len(closed_issues)} / open {len(open_issues)}")
        lines.append("")
        if not issues:
            lines.append(f"_no issues carry label `{cycle}`_")
            lines.append("")
            continue
        lines.append("| # | Title | Owner | State |")
        lines.append("|---|---|---|---|")
        for issue in sorted(issues, key=lambda i: (str(i.get("state", "")).upper() != "OPEN", i.get("number", 0))):
            title = str(issue.get("title", "")).replace("|", "\\|")
            state = str(issue.get("state", "?")).upper()
            lines.append(
                f"| [#{issue.get('number', '?')}]({issue.get('url', '')}) | {title} "
                f"| {issue_owner(issue)} | {state} |"
            )
        lines.append("")
    total = grand_open + grand_closed
    pct = f" ({grand_closed * 100 // total}% done)" if total else ""
    lines.append(f"**Total: closed {grand_closed} / open {grand_open}{pct}** — "
                 "open issues get carried over (new cycle label + new ETA) or dropped in the meeting.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Markdown burndown report for a weekly cycle label, per product repo."
    )
    parser.add_argument("--cycle", required=True, help="cycle label, e.g. weekly-2026-07-21")
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        metavar="OWNER/NAME",
        help=f"repo to report (repeatable; default: {', '.join(DEFAULT_REPOS)})",
    )
    parser.add_argument(
        "--issues-json",
        metavar="PATH",
        help='offline fixture: JSON {"owner/name": [issue, ...]} instead of gh',
    )
    args = parser.parse_args(argv)

    if not CYCLE_RE.match(args.cycle):
        print(
            f"ERROR  bad cycle label {args.cycle!r} — expected weekly-YYYY-MM-DD",
            file=sys.stderr,
        )
        return 2

    repos = args.repos or DEFAULT_REPOS

    fixture: dict[str, list[dict]] | None = None
    if args.issues_json:
        try:
            fixture = json.loads(Path(args.issues_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR  cannot read fixture {args.issues_json}: {exc}", file=sys.stderr)
            return 2

    issues_by_repo: dict[str, list[dict]] = {}
    for repo in repos:
        try:
            issues_by_repo[repo] = (
                fixture.get(repo, []) if fixture is not None else gh_cycle_issues(repo, args.cycle)
            )
        except RuntimeError as exc:
            print(f"ERROR  {exc}", file=sys.stderr)
            return 2

    print(render(args.cycle, repos, issues_by_repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
