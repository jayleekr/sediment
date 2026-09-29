#!/usr/bin/env python3
"""Validate the objective GitHub properties of a PR-sized closing issue."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any, Callable


CLOSING_TARGET = re.compile(
    r"(?i)\b(?:close(?:s|d)?|fix(?:es|ed)?|resolve(?:s|d)?):?\s+"
    r"(?:https://github\.com/(?P<url_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<url_number>[0-9]+)"
    r"|(?:(?P<short_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<short_number>[0-9]+))"
)


def positive_issue(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("issue number must be positive")
    return number


def closing_issue_targets(body: str, repo: str) -> list[tuple[str, int]]:
    targets = []
    for match in CLOSING_TARGET.finditer(body):
        target_repo = match.group("url_repo") or match.group("short_repo") or repo
        target_number = match.group("url_number") or match.group("short_number")
        targets.append((target_repo, int(target_number)))
    return targets


def validate_scoped_issue(issue: dict[str, Any], repo: str, number: int, body: str) -> dict[str, Any]:
    """Validate objective issue properties; semantic fit remains an agent judgment."""
    if issue.get("number") != number:
        raise ValueError(f"scoped issue response does not match #{number}")
    if issue.get("pull_request") is not None:
        raise ValueError(f"scoped issue #{number} is a pull request, not an issue")
    if issue.get("state") != "open":
        raise ValueError(f"scoped issue #{number} must be open")
    labels = {
        str(label.get("name") if isinstance(label, dict) else label).strip().lower()
        for label in issue.get("labels", [])
    }
    issue_type = issue.get("type") or issue.get("issue_type") or {}
    type_name = issue_type.get("name", "") if isinstance(issue_type, dict) else str(issue_type)
    title = str(issue.get("title") or "").strip()
    if "epic" in labels or type_name.strip().lower() == "epic" or re.match(r"(?i)^(?:\[[^\]]+\]\s*)*\[?epic\]?(?:\s|:)", title):
        raise ValueError(f"scoped issue #{number} is an Epic; use a PR-sized child work issue")
    if not issue.get("created_at"):
        raise ValueError(f"scoped issue #{number} has no creation timestamp")
    targets = closing_issue_targets(body, repo)
    if not targets:
        raise ValueError(f"PR body must close scoped issue #{number}")
    expected = (repo, number)
    if any(target != expected for target in targets):
        raise ValueError(f"PR body closing targets must contain only scoped issue {repo}#{number}")
    return {"repo": repo, "number": number, "url": issue.get("html_url"), "created_at": issue["created_at"]}


def gh_issue(repo: str, number: int) -> dict[str, Any]:
    proc = subprocess.run(
        ["gh", "api", "--method", "GET", f"repos/{repo}/issues/{number}"],
        text=True, capture_output=True, check=False,
    )
    if proc.returncode:
        raise ValueError(f"cannot read scoped issue {repo}#{number}: {proc.stderr.strip() or 'GitHub API failed'}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"scoped issue {repo}#{number} returned invalid JSON") from exc


def main(argv: list[str] | None = None, fetch: Callable[[str, int], dict[str, Any]] = gh_issue) -> int:
    parser = argparse.ArgumentParser(description="Validate one PR-sized GitHub closing issue.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", type=positive_issue)
    parser.add_argument("--body", required=True)
    args = parser.parse_args(argv)
    targets = closing_issue_targets(args.body, args.repo)
    if args.issue is None:
        unique = list(dict.fromkeys(targets))
        if len(unique) != 1 or unique[0][0] != args.repo:
            raise ValueError("PR body must close exactly one same-repository scoped issue")
        args.issue = unique[0][1]
    result = validate_scoped_issue(fetch(args.repo, args.issue), args.repo, args.issue, args.body)
    print(json.dumps({"status": "valid", "scoped_issue": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"hype-pr issue guard: {exc}") from exc
