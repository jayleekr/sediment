#!/usr/bin/env python3
"""hypeproof weekly-loop announcement — the Tuesday broadcast for a cycle.

Canonical source in hypeproof-harness; vendored into each consumer repo via
sync.sh. Standard library only (no package manager) and all GitHub access goes
through the `gh` CLI — no tokens are read or stored here.

`check.py` and `burndown.py` answer "are this cycle's issues well-formed?" and
"what's the raw list?". This adds the missing step the weekly loop needs: a
single human-facing ANNOUNCEMENT that tells each member what THEY own this
cycle, what carried over unfinished from last cycle, and how the tracked
milestones (workshop dates) are progressing. It renders markdown ready to
publish as an Artifact and to post to Discord.

USAGE:
    announce.py --cycle weekly-2026-07-27
    announce.py --cycle weekly-2026-07-27 --prev-cycle weekly-2026-07-20
    announce.py --cycle weekly-2026-07-27 --milestone-repo jayleekr/hypeproof-studio
    announce.py --cycle weekly-2026-07-27 --issues-json fixtures.json  # offline

`--prev-cycle` defaults to the Monday one week before the cycle date, so the
carry-over section fills automatically. `--issues-json` bypasses `gh` for
deterministic tests: a JSON object mapping "owner/name" to a list of gh-shaped
issue dicts. `--milestones-json` similarly stubs milestone data
({"owner/name": [milestone, ...]}).

EXIT CODES:
    0  announcement generated
    2  config error / bad cycle label / gh failure
"""
from __future__ import annotations

import argparse
import datetime as dt
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

CYCLE_RE = re.compile(r"^weekly-(\d{4})-(\d{2})-(\d{2})$")
OWNER_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:Owner|담당자?)(?:\*\*)?\s*[:：]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def cycle_date(cycle: str) -> dt.date:
    m = CYCLE_RE.match(cycle)
    if not m:
        raise ValueError(f"bad cycle label {cycle!r} — expected weekly-YYYY-MM-DD")
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def prev_cycle_label(cycle: str) -> str:
    """The cycle one week earlier — where unfinished work carries over FROM."""
    return "weekly-" + (cycle_date(cycle) - dt.timedelta(days=7)).isoformat()


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


def gh_milestones(repo: str) -> list[dict]:
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/milestones?state=all&sort=due_on&direction=asc"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"gh milestones failed for {repo}: {detail}")
    return json.loads(proc.stdout or "[]")


def issue_owner(issue: dict) -> str:
    """Owner line from the body wins; fall back to the first assignee."""
    m = OWNER_LINE_RE.search(issue.get("body") or "")
    if m:
        owner = m.group(1).strip()
        return owner if owner.startswith("@") else f"@{owner.lstrip('@')}"
    assignees = issue.get("assignees") or []
    if assignees:
        login = assignees[0].get("login") if isinstance(assignees[0], dict) else assignees[0]
        if login:
            return f"@{login}"
    return "—"


def is_open(issue: dict) -> bool:
    return str(issue.get("state", "")).upper() == "OPEN"


def render(
    cycle: str,
    prev_cycle: str,
    repos: list[str],
    this_cycle: dict[str, list[dict]],
    prev_open: dict[str, list[dict]],
    milestones: dict[str, list[dict]],
) -> str:
    lines: list[str] = [
        f"# HypeProof 주간 공지 — {cycle}",
        "",
        f"이번 사이클(마감 {cycle_date(cycle).isoformat()}) 배정과 지난 사이클 이월, 마일스톤 현황입니다.",
        "",
        "## 이번 주 배정 (담당자별)",
        "",
    ]

    # Group this cycle's issues by owner across all repos.
    by_owner: dict[str, list[tuple[str, dict]]] = {}
    total_new = 0
    for repo in repos:
        for issue in this_cycle.get(repo, []):
            by_owner.setdefault(issue_owner(issue), []).append((repo, issue))
            total_new += 1

    if not total_new:
        lines.append(f"_이번 사이클(`{cycle}`) 라벨이 붙은 이슈가 아직 없습니다._")
        lines.append("")
    else:
        for owner in sorted(by_owner, key=lambda o: (o == "—", o.lower())):
            items = by_owner[owner]
            lines.append(f"### {owner} — {len(items)}건")
            lines.append("")
            for repo, issue in sorted(items, key=lambda ri: (ri[0], ri[1].get("number", 0))):
                short = repo.split("/", 1)[-1]
                title = str(issue.get("title", "")).replace("|", "\\|")
                state = "" if is_open(issue) else " ✅"
                lines.append(
                    f"- [{short}#{issue.get('number', '?')}]({issue.get('url', '')}) {title}{state}"
                )
            lines.append("")

    # Carry-over: last cycle's still-open issues.
    carried = [
        (repo, issue)
        for repo in repos
        for issue in prev_open.get(repo, [])
        if is_open(issue)
    ]
    lines.append(f"## 지난 사이클 이월 (`{prev_cycle}` 미완)")
    lines.append("")
    if not carried:
        lines.append("_이월된 미완 항목이 없습니다. 지난 사이클 전부 마감._")
        lines.append("")
    else:
        lines.append(f"미완 {len(carried)}건 — 이번 사이클로 이월되었습니다.")
        lines.append("")
        lines.append("| # | Title | Owner |")
        lines.append("|---|---|---|")
        for repo, issue in sorted(carried, key=lambda ri: (ri[0], ri[1].get("number", 0))):
            short = repo.split("/", 1)[-1]
            title = str(issue.get("title", "")).replace("|", "\\|")
            lines.append(
                f"| [{short}#{issue.get('number', '?')}]({issue.get('url', '')}) "
                f"| {title} | {issue_owner(issue)} |"
            )
        lines.append("")

    # Milestone progress.
    lines.append("## 마일스톤 현황")
    lines.append("")
    ms_rows: list[str] = []
    for repo in repos:
        for ms in milestones.get(repo, []):
            openc = int(ms.get("open_issues", 0) or 0)
            closedc = int(ms.get("closed_issues", 0) or 0)
            total = openc + closedc
            pct = f"{closedc * 100 // total}%" if total else "—"
            due = ms.get("due_on") or ""
            due = due[:10] if due else "미정"
            title = str(ms.get("title", "")).replace("|", "\\|")
            state = str(ms.get("state", "")).lower()
            flag = " (closed)" if state == "closed" else ""
            ms_rows.append(
                f"| {repo.split('/')[-1]} | {title}{flag} | {due} | {closedc}/{total} ({pct}) |"
            )
    if not ms_rows:
        lines.append("_추적 중인 마일스톤이 없습니다._")
        lines.append("")
    else:
        lines.append("| Repo | 마일스톤 | 마감 | 진행 |")
        lines.append("|---|---|---|---|")
        lines.extend(ms_rows)
        lines.append("")

    lines.append("---")
    lines.append(
        "_원칙: 기록되지 않은 일은 존재하지 않는다. 배정/이월 이슈는 담당자가 "
        "ETA까지 진행하고, 마일스톤은 각 이벤트 데드라인 기준으로 점검한다._"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly announcement markdown: per-owner assignments + carry-over + milestones."
    )
    parser.add_argument("--cycle", required=True, help="cycle label, e.g. weekly-2026-07-27")
    parser.add_argument("--prev-cycle", help="carry-over source cycle (default: cycle minus 7 days)")
    parser.add_argument(
        "--repo", action="append", dest="repos", metavar="OWNER/NAME",
        help=f"repo to include (repeatable; default: {', '.join(DEFAULT_REPOS)})",
    )
    parser.add_argument(
        "--milestone-repo", action="append", dest="milestone_repos", metavar="OWNER/NAME",
        help="repo to read milestones from (repeatable; default: same as --repo)",
    )
    parser.add_argument("--issues-json", metavar="PATH", help="offline issues fixture")
    parser.add_argument("--milestones-json", metavar="PATH", help="offline milestones fixture")
    args = parser.parse_args(argv)

    try:
        cycle_date(args.cycle)
    except ValueError as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return 2

    prev = args.prev_cycle or prev_cycle_label(args.cycle)
    try:
        cycle_date(prev)
    except ValueError as exc:
        print(f"ERROR  bad --prev-cycle: {exc}", file=sys.stderr)
        return 2

    repos = args.repos or DEFAULT_REPOS
    milestone_repos = args.milestone_repos or repos

    issues_fixture = None
    milestones_fixture = None
    try:
        if args.issues_json:
            issues_fixture = json.loads(Path(args.issues_json).read_text(encoding="utf-8"))
        if args.milestones_json:
            milestones_fixture = json.loads(Path(args.milestones_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR  cannot read fixture: {exc}", file=sys.stderr)
        return 2

    this_cycle: dict[str, list[dict]] = {}
    prev_open: dict[str, list[dict]] = {}
    milestones: dict[str, list[dict]] = {}
    try:
        for repo in repos:
            if issues_fixture is not None:
                # Fixture maps "repo::cycle" if present, else "repo" for this cycle only.
                this_cycle[repo] = issues_fixture.get(f"{repo}::{args.cycle}",
                                                       issues_fixture.get(repo, []))
                prev_open[repo] = issues_fixture.get(f"{repo}::{prev}", [])
            else:
                this_cycle[repo] = gh_cycle_issues(repo, args.cycle)
                prev_open[repo] = gh_cycle_issues(repo, prev)
        for repo in milestone_repos:
            if milestones_fixture is not None:
                milestones[repo] = milestones_fixture.get(repo, [])
            else:
                milestones[repo] = gh_milestones(repo)
    except RuntimeError as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return 2

    print(render(args.cycle, prev, repos, this_cycle, prev_open, milestones))
    return 0


if __name__ == "__main__":
    sys.exit(main())
