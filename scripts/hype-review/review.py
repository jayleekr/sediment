#!/usr/bin/env python3
"""Generate a role-aware PR review worksheet for HypeProof reviewers."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OWNER = "jayleekr"
DEFAULT_ADMINS = {"jayleekr", "JeHyeong2"}
DEFAULT_WRITERS = {"ico1036", "xoqhdgh1002", "JinyongShin", "TJ-kr"}
DEFAULT_REVIEW_LENSES: dict[str, list[str]] = {
    "jayleekr": ["maintainer", "product", "security", "deploy", "docs"],
    "JeHyeong2": ["maintainer", "backend", "security"],
    "ico1036": ["contributor", "docs"],
    "xoqhdgh1002": ["contributor", "product"],
    "JinyongShin": ["backend", "deploy"],
    "TJ-kr": ["frontend", "product"],
}


@dataclass(frozen=True)
class Lens:
    name: str
    summary: str
    questions: tuple[str, ...]


BASE_QUESTIONS: tuple[str, ...] = (
    "이 PR이 닫는 이슈와 실제 변경 범위가 일치하는가?",
    "가장 중요한 사용자/운영자 행동 변화는 무엇인가?",
    "실패했을 때 피해를 받는 사람, 데이터, 배포 경로는 무엇인가?",
    "작성자가 적은 테스트가 변경 위험을 직접 증명하는가?",
    "문서, 런북, 환경 변수, 릴리즈 노트 중 같이 바뀌어야 할 것이 남았는가?",
)


ROLE_LENSES: dict[str, Lens] = {
    "contributor": Lens(
        "contributor",
        "기여자로서 변경 의도, 재현성, 읽기 쉬운 피드백을 확인한다.",
        (
            "내가 이 코드를 다음 작업에서 이어받아도 맥락을 이해할 수 있는가?",
            "PR 설명만 보고 어떤 검증을 믿어도 되는지 알 수 있는가?",
            "질문은 막연한 의견이 아니라 작성자가 바로 답할 수 있는 형태인가?",
        ),
    ),
    "maintainer": Lens(
        "maintainer",
        "메인테이너로서 repo 정책, 장기 유지보수, merge 책임을 확인한다.",
        (
            "이 변경이 branch protection, CODEOWNERS, deploy, release 정책과 충돌하지 않는가?",
            "나중에 되돌릴 때 어떤 파일과 외부 상태를 같이 되돌려야 하는가?",
            "승인 기준을 낮추는 예외가 숨어 있지 않은가?",
        ),
    ),
    "product": Lens(
        "product",
        "제품 관점에서 사용자 가치, 표면 동작, 문서 일관성을 확인한다.",
        (
            "사용자가 체감하는 문제와 해결이 PR 본문에 명확한가?",
            "빈 상태, 에러 상태, 권한 없음 상태까지 제품 문장과 흐름이 맞는가?",
            "공개 문서나 링크가 실제 제품 상태와 같은 톤으로 유지되는가?",
        ),
    ),
    "frontend": Lens(
        "frontend",
        "UI 관점에서 상태, 접근성, 반응형, 배포 결과를 확인한다.",
        (
            "로딩, 실패, 권한 없음, 성공 상태가 서로 겹치거나 깜빡이지 않는가?",
            "모바일과 데스크톱에서 텍스트가 버튼/카드 밖으로 넘치지 않는가?",
            "사용자가 다음 행동을 추측하지 않고 수행할 수 있는가?",
        ),
    ),
    "backend": Lens(
        "backend",
        "백엔드 관점에서 API 계약, 데이터, 에러, 테스트 경계를 확인한다.",
        (
            "API 입력/출력 계약이 기존 호출자와 호환되는가?",
            "권한, tenant, 데이터 마이그레이션, 재시도 동작이 명시적으로 검증됐는가?",
            "테스트가 구현 세부가 아니라 깨지면 안 되는 계약을 잡고 있는가?",
        ),
    ),
    "security": Lens(
        "security",
        "보안 관점에서 인증, 인가, 시크릿, 공개 repo 노출을 확인한다.",
        (
            "시크릿, 토큰, OAuth client secret, 내부 URL이 diff나 문서에 노출되지 않았는가?",
            "인증과 인가가 분리돼 있고, 권한 없음이 우회되지 않는가?",
            "로그, 에러 메시지, 공개 문서가 공격자에게 쓸 만한 정보를 주지 않는가?",
        ),
    ),
    "deploy": Lens(
        "deploy",
        "배포 관점에서 CI, preview, rollback, 운영 알림을 확인한다.",
        (
            "이 변경은 Vercel/GitHub Actions/외부 secret이 언제 갱신돼야 하는지 설명하는가?",
            "배포 실패 시 되돌릴 수 있는 단위와 확인 URL이 명확한가?",
            "필수 status check가 실제 위험을 막고 있고 불필요한 수동 단계가 없는가?",
        ),
    ),
    "docs": Lens(
        "docs",
        "문서 관점에서 source of truth, 링크, 제품별 포맷을 확인한다.",
        (
            "문서의 원천 repo와 호스팅된 링크가 같은 내용을 가리키는가?",
            "내부 운영 문서와 공개 제품 문서의 경계가 지켜졌는가?",
            "링크, frontmatter, 제목 계층, 제품명 표기가 다른 repo와 같은 형식인가?",
        ),
    ),
}


RISK_LENSES: dict[str, Lens] = {
    "security": Lens(
        "security",
        "인증/인가/시크릿/공개 노출 위험",
        ROLE_LENSES["security"].questions,
    ),
    "deploy": Lens(
        "deploy",
        "CI/CD, preview, production, rollback 위험",
        ROLE_LENSES["deploy"].questions,
    ),
    "data": Lens(
        "data",
        "데이터 계약, 마이그레이션, tenant 격리 위험",
        (
            "기존 데이터가 새 코드에서 계속 읽히는가?",
            "마이그레이션 실패, 중복 실행, 부분 적용 상황이 고려됐는가?",
            "tenant/user 경계가 테스트로 직접 증명되는가?",
        ),
    ),
    "ui": Lens(
        "ui",
        "UI 상태, 접근성, 반응형 위험",
        ROLE_LENSES["frontend"].questions,
    ),
    "docs": Lens(
        "docs",
        "공개 문서, 링크, source of truth 위험",
        ROLE_LENSES["docs"].questions,
    ),
    "governance": Lens(
        "governance",
        "repo 정책, branch protection, reviewer policy 위험",
        ROLE_LENSES["maintainer"].questions,
    ),
}


PATH_RISK_RULES: tuple[tuple[str, str], ...] = (
    (r"(^|/)(auth|admin|oauth|secret|token|credential)", "security"),
    (r"(^|/)\.github/workflows/|vercel|fly\.toml|deploy|release", "deploy"),
    (r"(^|/)(migrations?|sql|schema|tenant|rls|database|data)/|\.sql$", "data"),
    (r"(^|/)(frontend|app|components|pages|public)/|\.tsx?$|\.css$", "ui"),
    (r"(^|/)docs?/|README|AGENTS|CLAUDE|OPENCLAW|\.md$", "docs"),
    (r"CODEOWNERS|branch[_-]?protection|policy/|repo-governance", "governance"),
)


def run_gh(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def current_login() -> str:
    proc = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"gh api user failed: {detail}")
    return proc.stdout.strip()


def parse_inline_list(value: str) -> list[str]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    return [item.strip() for item in value[1:-1].split(",") if item.strip()]


def parse_members(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "admins": set(DEFAULT_ADMINS),
            "writers": set(DEFAULT_WRITERS),
            "review_lenses": {login: list(lenses) for login, lenses in DEFAULT_REVIEW_LENSES.items()},
        }

    members: dict[str, Any] = {"admins": set(), "writers": set(), "review_lenses": {}}
    current: str | None = None
    in_review_lenses = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line in ("admins:", "writers:"):
            current = line[:-1]
            in_review_lenses = False
            continue
        if line == "review_lenses:":
            current = None
            in_review_lenses = True
            continue
        if current and line.startswith("- "):
            members[current].add(line[2:].strip())
            continue
        if in_review_lenses and raw.startswith("  ") and ":" in line:
            login, value = line.split(":", 1)
            lenses = [lens for lens in parse_inline_list(value) if lens in ROLE_LENSES]
            if lenses:
                members["review_lenses"][login.strip()] = lenses
    return members


def parse_pr_ref(pr_ref: str, repo: str | None) -> tuple[str | None, str]:
    match = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_ref)
    if match:
        return match.group(1), match.group(2)
    return repo, pr_ref


def _monitor_module() -> Any:
    """Load hype-merge/monitor.py so review and merge share one gating verdict.

    Importing rather than re-implementing keeps a single definition of "who has
    approved" / "is this still waiting on review" across the two harnesses.
    """
    import importlib.util

    path = ROOT / "scripts" / "hype-merge" / "monitor.py"
    spec = importlib.util.spec_from_file_location("hype_merge_monitor", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load monitor helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("hype_merge_monitor", module)
    spec.loader.exec_module(module)
    return module


def needs_my_approval(pr: dict[str, Any], reviewer: str, monitor: Any) -> bool:
    """Would this reviewer's approval actually unblock the PR?

    True only when the PR is still waiting on review, the reviewer is not the
    author, and they have not already reviewed it.
    """
    login = reviewer.lstrip("@").lower()
    if monitor.login(pr.get("author")).lower() == login:
        return False
    if pr.get("isDraft"):
        return False
    already_reviewed = {
        author.lower() for author in monitor.latest_review_state_by_author(pr)
    }
    if login in already_reviewed:
        return False

    requirements = monitor.load_policy_review_requirements()
    repo = monitor.repo_name(pr)
    item = monitor.classify_pr(pr, required_non_author_approvals=requirements.get(repo, 0))
    # "waiting" means review is the only thing left; an explicit approval-count
    # blocker means the same on repos with no branch-protection signal.
    if item.status == "waiting":
        return True
    return any(
        blocker.startswith("policy_required_non_author_approvals:") for blocker in item.blockers
    )


def search_unrequested_approvals(reviewer: str, repo: str | None, limit: int) -> list[dict[str, Any]]:
    """Open PRs blocked on review that never sent this reviewer a request.

    Requesting reviewers is a step humans forget (which is why hype-pr automates
    it), and a missed request silently means a missed review — hypeprooflab#179
    sat waiting on exactly this gap.
    """
    monitor = _monitor_module()
    repos = [repo] if repo else monitor.load_policy_repos()
    found: list[dict[str, Any]] = []
    for name in repos:
        try:
            prs = monitor.fetch_open_prs(name, limit)
        except (RuntimeError, OSError):
            # A single unreadable repo must not blank the whole queue.
            continue
        for pr in prs:
            if needs_my_approval(pr, reviewer, monitor):
                item = dict(pr)
                item["_unrequested"] = True
                found.append(item)
    return found


def search_review_requests(reviewer: str, repo: str | None, owner: str, limit: int) -> list[dict[str, Any]]:
    fields = "author,isDraft,labels,number,repository,state,title,updatedAt,url"
    args = [
        "search",
        "prs",
        "--review-requested",
        reviewer,
        "--state",
        "open",
        "--archived=false",
        "--limit",
        str(limit),
        "--json",
        fields,
    ]
    if repo:
        args.extend(["--repo", repo])
    else:
        args.extend(["--owner", owner])
    data = run_gh(args)
    return data or []


def fetch_pr(repo: str | None, pr_ref: str) -> dict[str, Any]:
    parsed_repo, parsed_pr = parse_pr_ref(pr_ref, repo)
    fields = (
        "additions,author,baseRefName,changedFiles,deletions,files,headRefName,"
        "isDraft,labels,latestReviews,mergeStateStatus,number,reviewDecision,"
        "reviewRequests,statusCheckRollup,title,updatedAt,url"
    )
    args = ["pr", "view", parsed_pr, "--json", fields]
    if parsed_repo:
        args.extend(["--repo", parsed_repo])
    data = run_gh(args)
    if parsed_repo:
        data["repository"] = {"nameWithOwner": parsed_repo}
    return data


def offline_pr(repo: str | None, pr_ref: str | None, title: str | None) -> dict[str, Any] | None:
    if not pr_ref:
        return None
    parsed_repo, parsed_pr = parse_pr_ref(pr_ref, repo)
    return {
        "repository": {"nameWithOwner": parsed_repo or repo or "(unknown)"},
        "number": int(parsed_pr) if parsed_pr.isdigit() else parsed_pr,
        "title": title or "(offline PR)",
        "url": f"https://github.com/{parsed_repo}/pull/{parsed_pr}" if parsed_repo else "",
        "author": {"login": "작성자"},
        "isDraft": False,
        "reviewDecision": None,
        "mergeStateStatus": None,
        "changedFiles": 0,
        "additions": 0,
        "deletions": 0,
        "files": [],
        "labels": [],
        "reviewRequests": [],
        "latestReviews": [],
        "statusCheckRollup": [],
    }


def repo_name(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("nameWithOwner") or value.get("fullName") or value.get("name") or ""
    return str(value or "")


def login_name(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("login") or value.get("name") or ""
    return str(value or "")


def file_paths(pr: dict[str, Any] | None) -> list[str]:
    if not pr:
        return []
    paths: list[str] = []
    for item in pr.get("files") or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item["path"]))
        elif isinstance(item, str):
            paths.append(item)
    return paths


def detect_risks(paths: list[str]) -> list[str]:
    risks: list[str] = []
    for path in paths:
        for pattern, risk in PATH_RISK_RULES:
            if re.search(pattern, path, flags=re.IGNORECASE) and risk not in risks:
                risks.append(risk)
    return risks


def infer_roles(reviewer: str, explicit: list[str]) -> list[str]:
    members = parse_members(ROOT / "policy" / "members.yaml")
    login = reviewer.lstrip("@")
    inferred = list(members["review_lenses"].get(login, []))
    if not inferred:
        inferred = ["maintainer"] if login in members["admins"] else ["contributor"]
    return uniq(inferred + explicit)


def uniq(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def build_pack(
    *,
    reviewer: str,
    roles: list[str],
    risks: list[str],
    pr: dict[str, Any] | None,
    queue: list[dict[str, Any]],
) -> dict[str, Any]:
    paths = file_paths(pr)
    all_risks = uniq(risks + detect_risks(paths))
    role_sections = [ROLE_LENSES[name] for name in roles if name in ROLE_LENSES]
    risk_sections = [RISK_LENSES[name] for name in all_risks if name in RISK_LENSES]
    author = login_name((pr or {}).get("author")) or "작성자"
    return {
        "reviewer": reviewer,
        "pr": pr,
        "queue": queue,
        "roles": [section.name for section in role_sections],
        "risks": [section.name for section in risk_sections],
        "base_questions": list(BASE_QUESTIONS),
        "role_questions": [
            {"name": section.name, "summary": section.summary, "questions": list(section.questions)}
            for section in role_sections
        ],
        "risk_questions": [
            {"name": section.name, "summary": section.summary, "questions": list(section.questions)}
            for section in risk_sections
        ],
        "decision_gate": {
            "approve": [
                "변경 의도, 위험, 테스트가 모두 설명되고 직접 확인됐다.",
                "남은 의견이 있어도 merge를 막지 않는 non-blocking 코멘트다.",
            ],
            "comment": [
                "맥락 질문이나 개선 제안이 있지만, 답변만으로 해소될 수 있다.",
                "작성자가 어떤 증거를 주면 승인으로 바꿀지 명확하다.",
            ],
            "request_changes": [
                "보안, 데이터, 배포, 사용자 기능을 깨뜨릴 수 있는 blocker가 있다.",
                "파일/라인, 위험, 기대 수정, 재검증 방법을 함께 적을 수 있다.",
            ],
        },
        "reply_guide": {
            "approve": [
                f"@{author} 확인했습니다. 변경 범위와 테스트가 PR 설명과 맞습니다.",
                "제가 본 핵심 근거: <테스트/화면/파일>",
                "남은 의견은 merge를 막지 않습니다: <있으면 한 줄>",
            ],
            "comment": [
                f"@{author} 이 부분만 확인 부탁드립니다.",
                "질문: <구체 질문>",
                "왜 중요한지: <위험 또는 사용자 영향>",
                "답변/근거가 확인되면 승인하겠습니다.",
            ],
            "request_changes": [
                f"@{author} 이 PR은 merge 전에 수정이 필요합니다.",
                "Blocker: <파일/라인 또는 동작>",
                "Risk: <보안/데이터/배포/사용자 영향>",
                "Expected fix: <원하는 수정>",
                "Re-test: <작성자가 다시 돌려야 할 검증>",
            ],
        },
    }


def merge_queues(
    requested: list[dict[str, Any]], unrequested: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Requested items win; unrequested ones fill the gap without duplicating."""
    seen = {(repo_name(item.get("repository")), item.get("number")) for item in requested}
    extra = [
        item
        for item in unrequested
        if (repo_name(item.get("repository")), item.get("number")) not in seen
    ]
    return [*requested, *extra]


def render_queue(queue: list[dict[str, Any]]) -> list[str]:
    if not queue:
        return ["현재 조회된 리뷰 요청 PR이 없습니다."]
    lines = ["| Repo | PR | Title | Author | Updated | 요청 |", "|---|---:|---|---|---|---|"]
    for item in queue:
        repo = repo_name(item.get("repository"))
        number = item.get("number", "")
        title = str(item.get("title", "")).replace("|", "\\|")
        author = login_name(item.get("author"))
        updated = item.get("updatedAt") or ""
        url = item.get("url") or ""
        pr_cell = f"[#{number}]({url})" if url else f"#{number}"
        # Whether a request was actually sent is useful signal: an unrequested
        # item usually means the author skipped the reviewer-request step.
        origin = "미요청(승인 대기)" if item.get("_unrequested") else "요청됨"
        lines.append(f"| {repo} | {pr_cell} | {title} | @{author} | {updated} | {origin} |")
    return lines


def render_pr_snapshot(pr: dict[str, Any] | None) -> list[str]:
    if not pr:
        return ["특정 PR을 지정하지 않았습니다. 위 queue에서 하나를 골라 `--repo ... --pr ...`로 다시 실행하세요."]
    repo = repo_name(pr.get("repository"))
    author = login_name(pr.get("author"))
    labels = ", ".join(label.get("name", "") for label in pr.get("labels") or [] if isinstance(label, dict))
    lines = [
        f"- Repo: `{repo}`",
        f"- PR: [#{pr.get('number')}]({pr.get('url')}) `{pr.get('title')}`",
        f"- Author: @{author}",
        f"- Draft: {bool(pr.get('isDraft'))}",
        f"- Review decision: {pr.get('reviewDecision') or '(unknown)'}",
        f"- Merge state: {pr.get('mergeStateStatus') or '(unknown)'}",
        f"- Size: {pr.get('changedFiles', 0)} files, +{pr.get('additions', 0)} -{pr.get('deletions', 0)}",
    ]
    if labels:
        lines.append(f"- Labels: {labels}")
    paths = file_paths(pr)
    if paths:
        preview = ", ".join(f"`{path}`" for path in paths[:8])
        suffix = "" if len(paths) <= 8 else f" 외 {len(paths) - 8}개"
        lines.append(f"- Changed paths: {preview}{suffix}")
    return lines


def render_markdown(pack: dict[str, Any]) -> str:
    lines: list[str] = [
        "# hype-review",
        "",
        f"Reviewer: @{pack['reviewer']}",
        "",
        "이 하네스의 목적은 빨리 승인하는 것이 아니라, 각자 맡은 역할의 관점으로 질문하고 작성자에게 실행 가능한 피드백을 남기는 것이다.",
        "",
        "## Review Queue",
        "",
        *render_queue(pack["queue"]),
        "",
        "## PR Snapshot",
        "",
        *render_pr_snapshot(pack["pr"]),
        "",
        "## Review Steps",
        "",
        "1. PR 본문, 닫는 이슈, 변경 파일을 먼저 읽는다.",
        "2. 아래 공통 질문에 답이 안 나오면 작성자에게 질문한다.",
        "3. 내 역할 lens와 감지된 risk lens만 깊게 본다.",
        "4. 마지막에는 approve/comment/request changes 중 하나를 고르고 답변 가이드를 사용한다.",
        "",
        "## Common Questions",
        "",
    ]
    lines.extend(f"- {question}" for question in pack["base_questions"])
    lines.extend(["", "## Role Questions", ""])
    if pack["role_questions"]:
        for section in pack["role_questions"]:
            lines.extend([f"### {section['name']}", "", section["summary"], ""])
            lines.extend(f"- {question}" for question in section["questions"])
            lines.append("")
    else:
        lines.extend(["역할이 지정되지 않았습니다. `--role backend --role security`처럼 지정할 수 있습니다.", ""])
    lines.extend(["## Risk Questions", ""])
    if pack["risk_questions"]:
        for section in pack["risk_questions"]:
            lines.extend([f"### {section['name']}", "", section["summary"], ""])
            lines.extend(f"- {question}" for question in section["questions"])
            lines.append("")
    else:
        lines.extend(["변경 파일에서 고위험 lens가 감지되지 않았습니다. 그래도 공통 질문은 답해야 합니다.", ""])
    lines.extend(["## Decision Gate", ""])
    for decision, items in pack["decision_gate"].items():
        lines.extend([f"### {decision}", ""])
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    lines.extend(["## Reply Guide", ""])
    for decision, items in pack["reply_guide"].items():
        lines.extend([f"### {decision}", "", "```markdown"])
        lines.extend(items)
        lines.extend(["```", ""])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a HypeProof PR review worksheet.")
    parser.add_argument("--reviewer", default=None, help="GitHub login to check. Use @me with gh search, or pass a login.")
    parser.add_argument("--mine", action="store_true", help="Use the authenticated gh user as reviewer.")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="Owner to search when --repo is omitted.")
    parser.add_argument("--repo", default=None, help="Repository in owner/name form.")
    parser.add_argument("--pr", default=None, help="PR number or GitHub PR URL to build a focused worksheet.")
    parser.add_argument("--title", default=None, help="Offline title for --pr.")
    parser.add_argument(
        "--role",
        choices=sorted(ROLE_LENSES),
        action="append",
        default=[],
        help="Extra reviewer lens to add beyond policy/members.yaml. Can repeat.",
    )
    parser.add_argument("--risk", choices=sorted(RISK_LENSES), action="append", default=[], help="Risk lens to force. Can repeat.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum queued PRs to list.")
    parser.add_argument(
        "--requested-only",
        action="store_true",
        help="Only PRs that explicitly requested me. Default also lists PRs blocked on my approval.",
    )
    parser.add_argument("--offline", action="store_true", help="Do not call gh; generate from arguments only.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.mine and not args.offline:
            reviewer = current_login()
            search_reviewer = "@me"
        else:
            reviewer = args.reviewer or "@me"
            search_reviewer = reviewer

        roles = infer_roles(reviewer.lstrip("@"), args.role)
        if args.offline:
            pr = offline_pr(args.repo, args.pr, args.title)
            queue: list[dict[str, Any]] = []
        else:
            pr = fetch_pr(args.repo, args.pr) if args.pr else None
            queue = search_review_requests(search_reviewer, args.repo, args.owner, args.limit)
            if not args.requested_only:
                # A missing reviewer request must not hide a PR that is blocked
                # on this reviewer's approval (hypeprooflab#179).
                queue = merge_queues(
                    queue,
                    search_unrequested_approvals(reviewer, args.repo, args.limit),
                )

        pack = build_pack(
            reviewer=reviewer.lstrip("@"),
            roles=roles,
            risks=uniq(args.risk),
            pr=pr,
            queue=queue,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"hype-review: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(pack), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
