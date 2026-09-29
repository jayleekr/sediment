#!/usr/bin/env python3
"""Plan and execute HypeProof pull request workflow policy.

This tool is intentionally conservative:

- reviewer requests are explicit opt-in and exclude the PR author
- auto-merge is opt-in and only enabled when repo policy allows it and the
  detected risk is low enough
- dry-run is the default for commands that would mutate GitHub
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def refresh_canonical(canonical):
    """Fast-forward the canonical checkout to origin/main before delegating to it.

    A consumer that delegates to a stale or broken checkout runs yesterday's
    engine against today's policy, and the old failure mode only said
    "outdated" without saying what to do. This repairs the cheap cases and
    names the rest.

    Nothing is ever discarded. Uncommitted changes and commits that are not on
    origin/main stop the repair and are reported, because neither can be
    recreated once overwritten. Being left on a feature branch is repaired:
    the branch keeps its commits and only the checkout moves to main.

    Returns None when the checkout is current, otherwise a reason it was left
    alone. That reason is reported, not fatal: refusing to run because the
    canonical checkout has local edits punishes exactly the person developing
    Harness, whose tree is dirty by definition. Only a checkout that cannot
    serve the engine at all stops the run.

    Set HYPEPROOF_HARNESS_PIN to stay on a chosen revision.
    """
    if os.environ.get("HYPEPROOF_HARNESS_PIN"):
        return None

    def git(*args):
        return subprocess.run(["git", "-C", str(canonical), *args], text=True, capture_output=True)

    def out(*args):
        return git(*args).stdout.strip()

    if git("rev-parse", "--git-dir").returncode:
        return "not a git checkout"
    if git("fetch", "--quiet", "origin", "main").returncode:
        return "cannot fetch origin/main; check network access and credentials"
    if out("rev-parse", "HEAD") == out("rev-parse", "origin/main"):
        return None

    # Not out(): porcelain pads the status to two columns, so stripping the
    # output eats the leading space of the first line and its path loses a
    # character. Naming a file wrongly is worse than not naming it.
    dirty = sorted(line[3:] for line in git("status", "--porcelain").stdout.splitlines() if line.strip())
    if dirty:
        return "left alone, uncommitted changes: " + ", ".join(dirty[:5])
    branch = out("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main" and git("checkout", "main").returncode:
        return f"cannot leave branch {branch} for main"
    ahead = [line for line in out("log", "--oneline", "origin/main..HEAD").splitlines() if line]
    if ahead:
        return "left alone, local commits not on origin/main: " + ", ".join(ahead[:3])
    if git("merge", "--ff-only", "origin/main").returncode:
        return "fast-forward to origin/main failed"
    return None


BUNDLE_PATHS = ("scripts/hype-pr", "skills/hype-pr", "docs/AGENT-GUIDE.ko.md", "docs/HYPE-PR.ko.md")


def bundle_drift(canonical, root):
    """Report when the installed bundle no longer matches the canonical one.

    The engine that runs is always the canonical copy, so drift is not fatal and
    must not block work. The launcher, the skill and its docs are the installed
    copy though, and a launcher that predates a fix does not carry it. That is
    how the canonical-checkout repair sat inert in a consumer repo: the old
    launcher checked only that preparation.py existed and delegated in silence.

    Only a real difference in the bundle is reported. Harness commits that touch
    nothing here are not drift, and a consumer that went red every time Harness
    moved would be muted within a week.

    Returns None when the installed bundle is current, otherwise a message.
    """
    stamp = root / "scripts/hype-pr/HARNESS_VERSION"
    if not stamp.is_file():
        return None
    installed = stamp.read_text().strip()

    def git(*args):
        return subprocess.run(["git", "-C", str(canonical), *args], text=True, capture_output=True)

    if not installed or git("cat-file", "-e", installed + "^{commit}").returncode:
        return f"installed revision {installed or '(empty)'} is unknown to the canonical checkout"
    if git("diff", "--quiet", installed, "HEAD", "--", *BUNDLE_PATHS).returncode == 0:
        return None
    return f"installed bundle is from {installed[:7]} and the canonical bundle has changed since"


ROOT = Path(__file__).resolve().parents[2]
# Consumer copies delegate to the canonical checkout; policy/engine are never vendored.
if not (ROOT / "policy/repos.yaml").is_file():
    configured = os.environ.get("HYPEPROOF_HARNESS")
    if configured:
        canonical = Path(configured).expanduser().resolve()
    else:
        common = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"], text=True).strip()
        canonical = Path(common).parent.parent / "hypeproof-harness"
    target = canonical / "scripts/hype-pr/pr.py"
    if not target.is_file() or not (canonical / "policy/repos.yaml").is_file():
        raise SystemExit("hype-pr: canonical Harness checkout missing; clone hypeproof-harness as a sibling or set HYPEPROOF_HARNESS")
    unresolved = refresh_canonical(canonical)
    # Staleness keeps its own verdict: the automatic update is what should have
    # fixed it, so its reason belongs in that message rather than replacing it.
    if not (canonical / "scripts/hype-pr/preparation.py").is_file():
        raise SystemExit("hype-pr: canonical Harness checkout is outdated; update it to current main or set HYPEPROOF_HARNESS to an updated checkout"
                         + (f" (automatic update stopped: {unresolved})" if unresolved else ""))
    if unresolved:
        # Reported, not fatal. The checkout can serve the engine; it just was
        # not brought to main, and the reason is something only a human can
        # decide about.
        print(f"hype-pr: canonical Harness checkout at {canonical} was not updated: {unresolved}", file=sys.stderr)
    drift = bundle_drift(canonical, ROOT)
    if drift:
        print(f"hype-pr: {drift}; reinstall with: python3 {canonical}/scripts/hype-pr/install.py {ROOT}", file=sys.stderr)
    if __name__ == "__main__":
        os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
    raise RuntimeError("import hype-pr from the canonical Harness checkout")
sys.path.insert(0, str(ROOT / "scripts" / "repo-governance"))
from audit import load_policy, repo_full_name, validate_policy  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts" / "hype-pr"))


DEFAULT_OWNER = "jayleekr"
DEFAULT_BASE = "main"
AUTO_MERGE_METHOD = "squash"


PATH_RISK_RULES: tuple[tuple[str, str], ...] = (
    (r"(^|/)(auth|admin|oauth|secret|token|credential|permission|rbac)", "security"),
    (r"SECURITY\.md|\.pem$|\.key$|\.env", "security"),
    (r"(^|/)\.github/workflows/|vercel|fly\.toml|wrangler|deploy|release", "deploy"),
    (r"(^|/)(migrations?|sql|schema|tenant|rls|database|data)/|\.sql$", "data"),
    (r"package-lock\.json|pnpm-lock\.yaml|yarn\.lock|requirements.*\.txt|pyproject\.toml|Cargo\.lock", "dependency"),
    (r"CODEOWNERS|branch[_-]?protection|policy/|repo-governance", "governance"),
    (r"(^|/)docs?/|README|AGENTS|CLAUDE|OPENCLAW|\.md$", "docs"),
    (r"(^|/)(frontend|app|components|pages|public)/|\.tsx?$|\.css$", "ui"),
)

AUTO_MERGE_BLOCKING_RISKS = {"security", "deploy", "data", "dependency", "governance"}
AUTO_MERGE_BLOCKING_LABELS = {
    "human-needed",
    "security",
    "deploy",
    "data",
    "incident",
    "breaking-change",
    "do-not-merge",
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def uniq(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def parse_repo(value: str) -> str:
    if "/" in value:
        owner, name = value.split("/", 1)
        if owner and name:
            return value
    return f"{DEFAULT_OWNER}/{value}"


from issue_guard import positive_issue, validate_scoped_issue  # noqa: E402


def fetch_scoped_issue(repo: str, number: int) -> dict[str, Any]:
    path = f"repos/{repo}/issues/{number}"
    if os.environ.get("HYPE_PR_WORK_DIR"):
        from work_transport import exchange
        return exchange("read", {"path": path})
    response = run(["gh", "api", "--method", "GET", path])
    if response.returncode != 0:
        raise ValueError(f"cannot read scoped issue {repo}#{number}: {response.stderr.strip() or 'GitHub API failed'}")
    try:
        return json.loads(response.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"scoped issue {repo}#{number} returned invalid JSON") from exc


def active_members(policy: dict[str, Any]) -> list[str]:
    members = policy.get("members", {}).get("members", {})
    return uniq(list(members.get("admins", [])) + list(members.get("writers", [])))


def reviewers_for_author(policy: dict[str, Any], author: str | None) -> list[str]:
    author_norm = (author or "").lstrip("@").lower()
    return [
        login
        for login in active_members(policy)
        if login.lower() != author_norm
    ]


def find_repo(policy: dict[str, Any], repo_ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    wanted = parse_repo(repo_ref)
    for repo in policy["repos"].get("repositories", []):
        full = repo_full_name(repo)
        if wanted in (full, repo.get("name")):
            return repo, policy["profiles"][repo["profile"]]
    raise ValueError(f"unknown repo in policy: {repo_ref}")


def detect_risks(paths: list[str]) -> list[str]:
    risks: list[str] = []
    for path in paths:
        for pattern, risk in PATH_RISK_RULES:
            if re.search(pattern, path, flags=re.IGNORECASE) and risk not in risks:
                risks.append(risk)
    return risks


def required_checks(repo: dict[str, Any]) -> list[str]:
    return list(repo.get("required_status_checks") or [])


def plan(
    *,
    policy: dict[str, Any],
    repo_ref: str,
    author: str | None,
    paths: list[str],
    labels: list[str],
    draft: bool,
    auto_merge: bool,
    request_reviewers: bool = False,
) -> dict[str, Any]:
    repo, profile = find_repo(policy, repo_ref)
    full = repo_full_name(repo)
    profile_repo = profile.get("repository", {})
    risks = detect_risks(paths)
    eligible_reviewers = reviewers_for_author(policy, author)
    reviewer_logins = eligible_reviewers if request_reviewers else []
    profile_allows_auto_merge = bool(profile_repo.get("allow_auto_merge"))
    checks = required_checks(repo)

    blockers: list[str] = []
    if not auto_merge:
        blockers.append("auto_merge_not_requested")
    if draft:
        blockers.append("draft_pr")
    if not profile_allows_auto_merge:
        blockers.append("profile_disallows_auto_merge")
    if not checks:
        blockers.append("no_required_status_checks_declared")
    for risk in risks:
        if risk in AUTO_MERGE_BLOCKING_RISKS:
            blockers.append(f"risk:{risk}")
    for label in labels:
        if label in AUTO_MERGE_BLOCKING_LABELS:
            blockers.append(f"label:{label}")

    return {
        "status": "planned",
        "repo": full,
        "profile": repo.get("profile"),
        "author": author,
        "reviewers": reviewer_logins,
        "review_request": {
            "enabled": request_reviewers,
            "active_members": active_members(policy),
            "exclude_author": True,
            "eligible_reviewers": eligible_reviewers,
            "requested_reviewers": reviewer_logins,
        },
        "risk": {
            "paths": paths,
            "detected": risks,
            "auto_merge_blocking": [risk for risk in risks if risk in AUTO_MERGE_BLOCKING_RISKS],
        },
        "auto_merge": {
            "requested": auto_merge,
            "profile_allows": profile_allows_auto_merge,
            "eligible": not blockers,
            "method": AUTO_MERGE_METHOD,
            "blocked_by": uniq(blockers),
        },
        "required_status_checks": checks,
    }


def fetch_pr(repo: str, pr: str) -> dict[str, Any]:
    return gh_json([
        "pr",
        "view",
        pr,
        "--repo",
        parse_repo(repo),
        "--json",
        "author,files,isDraft,labels,number,title,url",
    ])


def gh_json(args: list[str]) -> Any:
    result = run(["gh", *args])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    text = result.stdout.strip()
    return json.loads(text) if text else None


def run(cmd: list[str], *, input_text: str | None = None) -> CommandResult:
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def command_request_reviewers(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    pr = fetch_pr(args.repo, args.pr)
    author = pr["author"]["login"]
    paths = [item["path"] for item in pr.get("files") or []]
    labels = [item["name"] for item in pr.get("labels") or []]
    planned = plan(
        policy=policy,
        repo_ref=args.repo,
        author=author,
        paths=paths,
        labels=labels,
        draft=bool(pr.get("isDraft")),
        auto_merge=False,
        request_reviewers=True,
    )
    reviewers = planned["reviewers"]
    result = {
        "plan": planned,
        "reviewer_commands": reviewer_commands(args.repo, args.pr, reviewers),
        "apply": args.apply,
    }
    if not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    result["reviewer_results"] = apply_reviewer_requests(args.repo, str(args.pr), reviewers)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if any(item["returncode"] != 0 for item in result["reviewer_results"]) else 0


def reviewer_commands(repo: str, pr_ref: str, reviewers: list[str]) -> list[list[str]]:
    return [
        [
            "gh",
            "pr",
            "edit",
            str(pr_ref),
            "--repo",
            parse_repo(repo),
            "--add-reviewer",
            reviewer,
        ]
        for reviewer in reviewers
    ]


def reviewer_cleanup_commands(repo: str, pr_ref: str, reviewers: list[str]) -> list[list[str]]:
    return [
        [
            "gh",
            "pr",
            "edit",
            str(pr_ref),
            "--repo",
            parse_repo(repo),
            "--remove-reviewer",
            reviewer,
        ]
        for reviewer in reviewers
    ]


def apply_reviewer_requests(repo: str, pr_ref: str, reviewers: list[str]) -> list[dict[str, Any]]:
    if os.environ.get("HYPE_PR_WORK_DIR"):
        from work_transport import exchange
        results = []
        for reviewer in reviewers:
            try:
                exchange("reviewer", {"repo": parse_repo(repo), "pr": pr_ref, "reviewer": reviewer})
                results.append({"reviewer": reviewer, "returncode": 0})
            except ValueError:
                results.append({"reviewer": reviewer, "returncode": 1,
                                "stderr": "Work reviewer request failed; no approval implied"})
        return results
    results: list[dict[str, Any]] = []
    for reviewer, cmd in zip(reviewers, reviewer_commands(repo, pr_ref, reviewers)):
        gh_result = run(cmd)
        results.append(
            {
                "reviewer": reviewer,
                "returncode": gh_result.returncode,
                "stdout": gh_result.stdout.strip(),
                "stderr": gh_result.stderr.strip(),
            }
        )
    return results


def apply_reviewer_cleanup(repo: str, pr_ref: str, reviewers: list[str]) -> list[dict[str, Any]]:
    if os.environ.get("HYPE_PR_WORK_DIR"):
        from work_transport import exchange
        results = []
        for reviewer in reviewers:
            try:
                exchange("unreviewer", {"repo": parse_repo(repo), "pr": pr_ref, "reviewer": reviewer})
                results.append({"reviewer": reviewer, "returncode": 0})
            except ValueError:
                results.append({"reviewer": reviewer, "returncode": 1,
                                "stderr": "Work reviewer cleanup failed; reconcile the created PR"})
        return results
    results: list[dict[str, Any]] = []
    commands = reviewer_cleanup_commands(repo, pr_ref, reviewers)
    for reviewer, cmd in zip(reviewers, commands):
        gh_result = run(cmd)
        results.append(
            {
                "reviewer": reviewer,
                "returncode": gh_result.returncode,
                "stdout": gh_result.stdout.strip(),
                "stderr": gh_result.stderr.strip(),
            }
        )
    return results


def preparation_module():
    sys.path.insert(0, str(ROOT / "scripts/hype-pr"))
    import preparation
    return preparation


def prepared_report(args, policy):
    return preparation_module().verify(
        getattr(args, "preparation", None), getattr(args, "checkout", "."),
        parse_repo(args.repo), args.base, args.head, active_members(policy))


def command_prepare(args, policy):
    module = preparation_module()
    report = module.inspect(args.checkout, parse_repo(args.repo), args.base, members=active_members(policy))
    if args.command == "inspect":
        if args.output:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            Path(args.output).chmod(0o600)
        if args.assessment_template:
            Path(args.assessment_template).write_text(json.dumps(module.assessment_template(report), ensure_ascii=False, indent=2) + "\n")
            Path(args.assessment_template).chmod(0o600)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["blockers"] else 0
    assessment = json.loads(Path(args.assessment).read_text())
    output = args.output or git_receipt_path(args.checkout)
    result = module.prepare(report, assessment, output)
    print(json.dumps({"status": "prepared", "receipt": str(result), "head": report["head"],
                      "agent_attestation": True, "human_approval": False}, ensure_ascii=False))
    return 0


def git_receipt_path(checkout):
    module = preparation_module()
    location = module.git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "hype-pr/preparation.json")
    return Path(location)


def command_create(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    work = bool(os.environ.get("HYPE_PR_WORK_DIR"))
    if work and args.auto_merge:
        raise ValueError("Work transport does not schedule auto-merge; follow existing review policy")
    labels = args.label or []
    preparation_required = parse_repo(args.repo) in json.loads((ROOT / "policy/change-impact.json").read_text())["repositories"]
    report = prepared_report(args, policy) if args.apply and preparation_required else None
    paths = report["paths"] if report else (args.path or [])
    planned = plan(
        policy=policy,
        repo_ref=args.repo,
        author=args.author,
        paths=paths,
        labels=labels,
        draft=args.draft,
        auto_merge=args.auto_merge,
        request_reviewers=args.request_reviewers,
    )
    body = args.body or ""
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    if report:
        body += preparation_module().summary(report)
    scoped_issue = None
    if args.apply:
        repo = parse_repo(args.repo)
        scoped_issue = validate_scoped_issue(fetch_scoped_issue(repo, args.issue), repo, args.issue, body)
    cmd = [
        "gh",
        "pr",
        "create",
        "--repo",
        parse_repo(args.repo),
        "--base",
        args.base,
        "--head",
        args.head,
        "--title",
        args.title,
        "--body",
        body,
    ]
    if args.draft:
        cmd.append("--draft")
    for label in labels:
        cmd.extend(["--label", label])

    result: dict[str, Any] = {
        "plan": planned,
        "preparation": {"required_for_apply": preparation_required, "verified": report is not None},
        "scoped_issue": scoped_issue or {"repo": parse_repo(args.repo), "number": args.issue, "verified": False},
        "create_command": cmd,
        "reviewer_commands": reviewer_commands(args.repo, "<created-pr>", planned["reviewers"]),
        "reviewer_cleanup_commands": reviewer_cleanup_commands(
            args.repo,
            "<created-pr>",
            [] if args.request_reviewers else planned["review_request"]["eligible_reviewers"],
        ),
        "apply": args.apply,
    }
    if not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if work:
        if report is None:
            raise ValueError("Work PR creation requires verified preparation")
        from work_transport import exchange
        outcome = exchange("create", {
            "repo": parse_repo(args.repo), "base": args.base, "head": args.head,
            "title": args.title, "body": body, "draft": args.draft,
            "labels": labels, "author": args.author,
            "expected": {"head": report["head"], "base": report["base_tip"],
                         "sources": report["source_commits"]},
        })
        created = CommandResult(0, outcome["url"], "")
    else:
        created = run(cmd)
    result["create"] = {
        "returncode": created.returncode,
        "stdout": created.stdout.strip(),
        "stderr": created.stderr.strip(),
    }
    if work:
        result["label_errors"] = outcome.get("label_errors", [])
    if created.returncode != 0:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return created.returncode

    pr_ref = created.stdout.strip().splitlines()[-1]
    result["reviewer_results"] = apply_reviewer_requests(args.repo, pr_ref, planned["reviewers"])
    cleanup_reviewers = [] if args.request_reviewers else planned["review_request"]["eligible_reviewers"]
    result["reviewer_cleanup_results"] = apply_reviewer_cleanup(args.repo, pr_ref, cleanup_reviewers)
    if planned["auto_merge"]["eligible"]:
        merge_cmd = [
            "gh",
            "pr",
            "merge",
            pr_ref,
            "--repo",
            parse_repo(args.repo),
            "--auto",
            f"--{AUTO_MERGE_METHOD}",
            "--delete-branch",
        ]
        merged = run(merge_cmd)
        result["auto_merge_command"] = merge_cmd
        result["auto_merge_result"] = {
            "returncode": merged.returncode,
            "stdout": merged.stdout.strip(),
            "stderr": merged.stderr.strip(),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    cleanup_failed = any(item["returncode"] != 0 for item in result["reviewer_cleanup_results"])
    return 1 if cleanup_failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HypeProof guarded PR creation harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="Plan explicit reviewers and auto-merge eligibility without GitHub calls.")
    plan_parser.add_argument("--repo", required=True, help="owner/name or policy repo name")
    plan_parser.add_argument("--author", required=True, help="GitHub login of PR author")
    plan_parser.add_argument("--path", action="append", default=[], help="Changed path. Can repeat.")
    plan_parser.add_argument("--label", action="append", default=[], help="PR label. Can repeat.")
    plan_parser.add_argument("--draft", action="store_true")
    plan_parser.add_argument("--auto-merge", action="store_true")
    plan_parser.add_argument("--request-reviewers", action="store_true", help="Opt in to all active non-author reviewers.")

    req_parser = sub.add_parser("request-reviewers", help="Request all active members on an existing PR.")
    req_parser.add_argument("--repo", required=True)
    req_parser.add_argument("--pr", required=True)
    req_parser.add_argument("--apply", action="store_true", help="Mutate GitHub. Omit for dry-run.")

    create_parser = sub.add_parser("create", help="Create a guarded PR without reviewers by default.")
    create_parser.add_argument("--repo", required=True)
    create_parser.add_argument("--head", required=True)
    create_parser.add_argument("--base", default=DEFAULT_BASE)
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--body", default="")
    create_parser.add_argument("--body-file")
    create_parser.add_argument("--author", required=True)
    create_parser.add_argument("--issue", required=True, type=positive_issue, help="Open PR-sized work issue in the target repository")
    create_parser.add_argument("--path", action="append", default=[])
    create_parser.add_argument("--label", action="append", default=[])
    create_parser.add_argument("--draft", action="store_true")
    create_parser.add_argument("--auto-merge", action="store_true")
    create_parser.add_argument("--request-reviewers", action="store_true", help="Opt in to all active non-author reviewers.")
    create_parser.add_argument("--checkout", default=".", help="Target worktree; inferred from the invocation directory")
    create_parser.add_argument("--preparation", help="Receipt from prepare, required for --apply")
    create_parser.add_argument("--apply", action="store_true", help="Mutate GitHub. Omit for dry-run.")

    for name in ("inspect", "prepare"):
        command = sub.add_parser(name, help="Inspect source links or bind the agent assessment before PR creation")
        command.add_argument("--repo", required=True)
        command.add_argument("--checkout", default=".")
        command.add_argument("--base", default=DEFAULT_BASE)
        command.add_argument("--output")
        if name == "inspect":
            command.add_argument("--assessment-template")
        else:
            command.add_argument("--assessment", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        policy = load_policy()
        findings = validate_policy(policy)
        if findings:
            print(json.dumps({"status": "policy-invalid", "findings": [f.as_dict() for f in findings]}, indent=2))
            return 4
        if args.command == "plan":
            data = plan(
                policy=policy,
                repo_ref=args.repo,
                author=args.author,
                paths=args.path,
                labels=args.label,
                draft=args.draft,
                auto_merge=args.auto_merge,
                request_reviewers=args.request_reviewers,
            )
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.command == "request-reviewers":
            return command_request_reviewers(args, policy)
        if args.command in {"inspect", "prepare"}:
            return command_prepare(args, policy)
        if args.command == "create":
            return command_create(args, policy)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"hype-pr: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
