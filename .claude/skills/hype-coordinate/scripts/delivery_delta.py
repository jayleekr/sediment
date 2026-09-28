#!/usr/bin/env python3
"""Emit HypeProof delivery changes plus a bounded, acknowledged backlog."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HYPEPROOF_DELIVERY_REPOS = (
    "jayleekr/hypeproof-harness",
    "jayleekr/hypeproof-studio",
    "jayleekr/hypeprooflab",
)
STATE_SCHEMA = 2
DEFAULT_BATCH_SIZE = 5


def harness_root() -> Path:
    configured = os.environ.get("HYPEPROOF_HARNESS")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.cwd().parent / "hypeproof-harness",
        Path.cwd() / "hypeproof-harness",
    ]
    for candidate in candidates:
        if candidate and (candidate / "scripts/hype-merge/monitor.py").is_file():
            return candidate.resolve()
    raise RuntimeError("set HYPEPROOF_HARNESS to the canonical harness checkout")


def default_state_file() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("run inside a HypeProof git checkout or pass --state-file")
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = Path.cwd() / common
    return common.resolve() / "hype-team" / "delivery-delta.json"


def current_queue(root: Path) -> list[dict[str, Any]]:
    dependency = subprocess.run(
        [sys.executable, "-c", "import yaml"],
        text=True,
        capture_output=True,
        check=False,
    )
    if dependency.returncode != 0:
        raise RuntimeError(
            "PyYAML is required; refusing to drop canonical review requirements"
        )
    command = [sys.executable, str(root / "scripts/hype-merge/monitor.py"), "--format", "json"]
    for repo in HYPEPROOF_DELIVERY_REPOS:
        command.extend(("--repo", repo))
    proc = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "hype-merge monitor failed")
    raw = json.loads(proc.stdout)
    keep = (
        "repo", "number", "status", "blockers", "non_author_approvals",
        "checks_ok", "reviewDecision", "mergeStateStatus", "headRefOid",
        "autoMergeEnabled", "url",
    )
    return sorted(
        ({key: item.get(key) for key in keep} for item in raw),
        key=lambda item: (str(item["repo"]), int(item["number"])),
    )


def current_issues() -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for repo in HYPEPROOF_DELIVERY_REPOS:
        proc = subprocess.run(
            [
                "gh", "issue", "list", "--repo", repo, "--state", "open",
                "--limit", "1000", "--json",
                "number,title,updatedAt,labels,assignees,url",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"issue scan failed for {repo}")
        for raw in json.loads(proc.stdout):
            issues.append({
                "repo": repo,
                "number": raw.get("number"),
                "title": raw.get("title"),
                "updatedAt": raw.get("updatedAt"),
                "labels": sorted(
                    label.get("name", "") for label in raw.get("labels", [])
                ),
                "assignees": sorted(
                    assignee.get("login", "") for assignee in raw.get("assignees", [])
                ),
                "url": raw.get("url"),
            })
    return sorted(issues, key=lambda item: (str(item["repo"]), int(item["number"])))


def keyed(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{item['repo']}#{item['number']}": item for item in items}


def delta(
    before: dict[str, dict[str, Any]], now: dict[str, dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any] | None]]:
    return {
        key: {"before": before.get(key), "after": now.get(key)}
        for key in sorted(set(before) | set(now))
        if before.get(key) != now.get(key)
    }


def work_record(kind: str, key: str, reason: str, payload: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        {"kind": kind, "key": key, "reason": reason, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "kind": kind,
        "key": key,
        "reason": reason,
        "token": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20],
        "payload": payload,
    }


def is_existing_intake_issue(item: dict[str, Any]) -> bool:
    """Bound migration to requirement-bearing issues instead of all historical issues."""
    title = str(item.get("title", "")).lower()
    labels = {str(label).lower() for label in item.get("labels", [])}
    return (
        "epic" in title
        or "epic" in labels
        or "intent" in labels
        or "requirement" in labels
    )


def priority(record: dict[str, Any]) -> tuple[int, int, str]:
    payload = record.get("payload", {})
    current = payload.get("after") if "after" in payload else payload
    number = int((current or payload.get("before") or {}).get("number", 0))
    return (
        0 if record.get("reason") == "changed" else 1,
        0 if record.get("kind") == "pull_request" else 1,
        f"{999999999 - number:09d}:{record.get('key', '')}",
    )


def write_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp = state_file.with_suffix(state_file.suffix + ".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(state_file)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--ack",
        metavar="TOKEN",
        help="acknowledge one emitted backlog token without polling GitHub",
    )
    args = parser.parse_args()
    try:
        state_file = args.state_file or default_state_file()
        if args.batch_size < 1 or args.batch_size > 20:
            raise ValueError("--batch-size must be between 1 and 20")
        saved: dict[str, Any] = {}
        if state_file.exists():
            saved = json.loads(state_file.read_text(encoding="utf-8"))
        if args.ack:
            pending = saved.get("pending")
            if not isinstance(pending, dict):
                raise RuntimeError("no acknowledged-backlog state exists yet")
            matches = [key for key, value in pending.items() if value.get("token") == args.ack]
            if len(matches) != 1:
                raise RuntimeError("ack token is absent or ambiguous; refusing stale acknowledgment")
            key = matches[0]
            del pending[key]
            saved["pending"] = pending
            write_state(state_file, saved)
            print(json.dumps({
                "status": "acknowledged",
                "key": key,
                "remaining": len(pending),
            }, ensure_ascii=False, separators=(",", ":")))
            return 0

        now_prs = current_queue(harness_root())
        now_issues = current_issues()
        before_prs: list[dict[str, Any]] = saved.get(
            "pull_requests", saved.get("queue", [])
        )
        before_issues: list[dict[str, Any]] = saved.get("issues", [])
        old_prs, new_prs = keyed(before_prs), keyed(now_prs)
        old_issues, new_issues = keyed(before_issues), keyed(now_issues)
        changed_prs = delta(old_prs, new_prs)
        changed_issues = delta(old_issues, new_issues)
        baseline = not before_prs and not before_issues
        migration = bool(saved) and "pending" not in saved
        pending: dict[str, dict[str, Any]] = dict(saved.get("pending", {}))

        if baseline or migration:
            for key, item in new_prs.items():
                pending[key] = work_record("pull_request", key, "existing", item)
            for key, item in new_issues.items():
                if is_existing_intake_issue(item):
                    pending[key] = work_record("issue", key, "existing_intake", item)

        if not baseline:
            for key, payload in changed_prs.items():
                pending[key] = work_record("pull_request", key, "changed", payload)
            for key, payload in changed_issues.items():
                pending[key] = work_record("issue", key, "changed", payload)

        actionable = sorted(pending.values(), key=priority)[:args.batch_size]
        changed = bool(changed_prs or changed_issues or actionable)
        write_state(state_file, {
            "schema": STATE_SCHEMA,
            "pull_requests": now_prs,
            "issues": now_issues,
            "pending": pending,
        })
        print(json.dumps({
            "status": "baseline" if baseline else ("changed" if changed else "unchanged"),
            "changed": changed or baseline,
            "pull_requests": len(now_prs),
            "issues": len(now_issues),
            "pending": len(pending),
            "delta": {
                "pull_requests": changed_prs,
                "issues": changed_issues,
                "backlog": actionable,
            },
        }, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "unknown", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
