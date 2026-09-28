#!/usr/bin/env python3
"""Request every active HypeProof member on open pull requests."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OWNER = "jayleekr"
DEFAULT_MEMBERS = ["jayleekr", "JeHyeong2", "ico1036", "xoqhdgh1002", "JinyongShin", "TJ-kr"]
REVIEWED_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}


@dataclass(frozen=True)
class ReviewerAction:
    repo: str
    number: int
    title: str
    url: str
    author: str
    reviewer: str
    status: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "reviewer": self.reviewer,
            "status": self.status,
            "reason": self.reason,
        }


def run_gh(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    text = proc.stdout.strip()
    return json.loads(text) if text else None


def run_gh_text(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    return proc.returncode, (proc.stderr.strip() or proc.stdout.strip())


def uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def load_members(path: Path) -> list[str]:
    if not path.exists():
        return list(DEFAULT_MEMBERS)
    try:
        import yaml  # type: ignore
    except ImportError:
        return list(DEFAULT_MEMBERS)

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    members = doc.get("members") or {}
    return uniq(list(members.get("admins") or []) + list(members.get("writers") or [])) or list(DEFAULT_MEMBERS)


def login(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or value.get("name") or "")
    return str(value or "")


def repo_name(pr: dict[str, Any]) -> str:
    repo = pr.get("repository")
    if isinstance(repo, dict):
        return str(repo.get("nameWithOwner") or repo.get("fullName") or repo.get("name") or "")
    return str(repo or "")


def title(pr: dict[str, Any]) -> str:
    return str(pr.get("title") or "")


def request_logins(pr: dict[str, Any]) -> set[str]:
    return {
        login(item).lower()
        for item in pr.get("reviewRequests") or []
        if login(item)
    }


def reviewed_logins(pr: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for review in pr.get("latestReviews") or []:
        if not isinstance(review, dict):
            continue
        state = str(review.get("state") or "")
        reviewer = login(review.get("author"))
        if state in REVIEWED_STATES and reviewer:
            out.add(reviewer.lower())
    return out


def normalize_pr(base: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    pr = dict(base)
    if detail:
        pr.update(detail)
    if "repository" not in pr and "repo" in pr:
        pr["repository"] = {"nameWithOwner": pr["repo"]}
    return pr


def fetch_open_prs(owner: str, repos: list[str], limit: int) -> list[dict[str, Any]]:
    if repos:
        found: list[dict[str, Any]] = []
        for repo in repos:
            listed = run_gh([
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,author,url",
            ]) or []
            for item in listed:
                item["repository"] = {"nameWithOwner": repo}
            found.extend(listed)
        return found

    return run_gh([
        "search",
        "prs",
        "--owner",
        owner,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "repository,number,title,author,url",
    ]) or []


def fetch_review_detail(repo: str, number: int) -> dict[str, Any]:
    return run_gh([
        "pr",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        "reviewRequests,latestReviews",
    ]) or {}


def pending_invite_logins(pr: dict[str, Any]) -> set[str]:
    return {
        login(invite.get("invitee") if isinstance(invite, dict) else invite).lower()
        for invite in pr.get("_pendingInvites") or []
        if login(invite.get("invitee") if isinstance(invite, dict) else invite)
    }


def fetch_pending_invitations(repo: str) -> list[dict[str, Any]]:
    code, text = run_gh_text(["api", f"repos/{repo}/invitations"])
    if code != 0:
        return []
    try:
        data = json.loads(text) if text else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def plan_actions(prs: list[dict[str, Any]], members: list[str], *, apply: bool = False) -> list[ReviewerAction]:
    actions: list[ReviewerAction] = []
    for pr in prs:
        repo = repo_name(pr)
        number = int(pr.get("number") or 0)
        author = login(pr.get("author"))
        author_key = author.lower()
        requested = request_logins(pr)
        reviewed = reviewed_logins(pr)
        pending_invites = pending_invite_logins(pr)
        for reviewer in members:
            reviewer_key = reviewer.lower()
            status = "would_request"
            reason = "missing_review_request"
            if reviewer_key == author_key:
                status = "skipped"
                reason = "author"
            elif reviewer_key in requested:
                status = "skipped"
                reason = "already_requested"
            elif reviewer_key in reviewed:
                status = "skipped"
                reason = "already_reviewed"
            elif reviewer_key in pending_invites:
                status = "pending_invitation"
                reason = "member_invitation_pending"
            elif apply:
                code, detail = run_gh_text(["pr", "edit", str(number), "--repo", repo, "--add-reviewer", reviewer])
                if code == 0:
                    status = "requested"
                else:
                    status = "failed"
                    reason = detail.splitlines()[-1] if detail else "request_failed"
            actions.append(
                ReviewerAction(
                    repo=repo,
                    number=number,
                    title=title(pr),
                    url=str(pr.get("url") or ""),
                    author=author,
                    reviewer=reviewer,
                    status=status,
                    reason=reason,
                )
            )
    return actions


def enrich_prs(prs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    invites_by_repo: dict[str, list[dict[str, Any]]] = {}
    for pr in prs:
        repo = repo_name(pr)
        number = int(pr.get("number") or 0)
        if repo not in invites_by_repo:
            invites_by_repo[repo] = fetch_pending_invitations(repo)
        detail = fetch_review_detail(repo, number)
        normalized = normalize_pr(pr, detail)
        normalized["_pendingInvites"] = invites_by_repo[repo]
        enriched.append(normalized)
    return enriched


def render_markdown(actions: list[ReviewerAction]) -> str:
    counts = {
        status: sum(1 for action in actions if action.status == status)
        for status in ("would_request", "requested", "failed", "pending_invitation", "skipped")
    }
    lines = [
        "# hype-review reviewer request audit",
        "",
        (
            f"would_request={counts['would_request']} requested={counts['requested']} "
            f"failed={counts['failed']} pending_invitation={counts['pending_invitation']} "
            f"skipped={counts['skipped']}"
        ),
        "",
        "| Status | Repo | PR | Reviewer | Reason |",
        "|---|---|---:|---|---|",
    ]
    for action in actions:
        if action.status == "skipped":
            continue
        pr = f"[#{action.number}]({action.url})" if action.url else f"#{action.number}"
        lines.append(f"| {action.status} | `{action.repo}` | {pr} | @{action.reviewer} | {action.reason} |")
    if all(action.status == "skipped" for action in actions):
        lines.append("| skipped | - | - | - | no missing reviewer requests |")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Request all active HypeProof members on open PRs.")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="Owner to search when --repo is omitted.")
    parser.add_argument("--repo", action="append", default=[], help="Repository in owner/name form. Can repeat.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum open PRs to inspect.")
    parser.add_argument("--member", action="append", default=[], help="Override policy members. Can repeat.")
    parser.add_argument("--policy-members", default=str(ROOT / "policy" / "members.yaml"))
    parser.add_argument("--offline-file", help="JSON file containing PR objects with reviewRequests/latestReviews.")
    parser.add_argument("--apply", action="store_true", help="Mutate GitHub by sending missing reviewer requests.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        members = uniq(args.member) if args.member else load_members(Path(args.policy_members))
        if args.offline_file:
            data = json.loads(Path(args.offline_file).read_text(encoding="utf-8"))
            prs = data if isinstance(data, list) else [data]
            prs = [normalize_pr(pr) for pr in prs]
        else:
            prs = enrich_prs(fetch_open_prs(args.owner, args.repo, args.limit))
        actions = plan_actions(prs, members, apply=args.apply)
    except (RuntimeError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"hype-review request-reviewers: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps([action.as_dict() for action in actions], ensure_ascii=False, indent=2))
    else:
        print(render_markdown(actions), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
