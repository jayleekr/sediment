#!/usr/bin/env python3
"""HypeProof docs contract checker.

This intentionally uses only the Python standard library so each product repo
can vendor and run it without bootstrapping a package manager.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


REQUIRED_DEV_DOCS = [
    "docs/dev/00-overview.md",
    "docs/dev/01-architecture.md",
    "docs/dev/02-directory-structure.md",
    "docs/dev/03-runtime-flows.md",
    "docs/dev/04-requirements.md",
    "docs/dev/05-testing-requirements.md",
    "docs/dev/06-release-process.md",
    "docs/dev/07-operations.md",
    "docs/dev/08-ux-evidence.md",
]

REQUIRED_FRONTMATTER = [
    "title",
    "product",
    "doc_type",
    "status",
    "owner",
    "version",
    "last_reviewed",
    "audience",
    "source_paths",
    "quality_gates",
]

DOC_TYPE_BY_PATH = {
    "00-overview.md": "overview",
    "01-architecture.md": "architecture",
    "02-directory-structure.md": "directory",
    "03-runtime-flows.md": "runtime",
    "04-requirements.md": "requirements",
    "05-testing-requirements.md": "testing",
    "06-release-process.md": "release",
    "07-operations.md": "operations",
    "08-ux-evidence.md": "ux-evidence",
}


class Finding:
    def __init__(self, path: str, check: str, message: str, points: int) -> None:
        self.path = path
        self.check = check
        self.message = message
        self.points = points

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "check": self.check,
            "message": self.message,
            "points": self.points,
        }


def parse_scalar(value: str):
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            return [x.strip().strip("'\"") for x in value[1:-1].split(",") if x.strip()]
    return value


def parse_simple_yaml(text: str) -> dict[str, object]:
    """Parse the small YAML subset used by hypeproof.docs.yaml/frontmatter."""
    data: dict[str, object] = {}
    current_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(parse_scalar(line[4:]))
            continue
        if line.startswith("- ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(parse_scalar(line[2:]))
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2) or ""
        current_key = key
        data[key] = [] if value == "" else parse_scalar(value)
    return data


def read_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end == -1:
        return {}, raw
    return parse_simple_yaml(raw[4:end]), raw[end + 4 :]


def read_manifest(root: Path) -> dict[str, object]:
    manifest = root / "hypeproof.docs.yaml"
    if not manifest.exists():
        return {}
    return parse_simple_yaml(manifest.read_text(encoding="utf-8"))


def source_version(root: Path, version_source: str | None) -> str | None:
    if not version_source:
        return None
    path = root / version_source
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    if path.name == "package.json":
        try:
            value = json.loads(raw).get("version")
            return value if isinstance(value, str) else None
        except json.JSONDecodeError:
            return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', raw, flags=re.MULTILINE)
    return match.group(1) if match else None


def git_ref(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def add(finding_list: list[Finding], path: str, check: str, message: str, points: int) -> None:
    finding_list.append(Finding(path, check, message, points))


def check_links(root: Path, doc: Path, body: str, findings: list[Finding]) -> None:
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
        if re.match(r"^[a-z]+://", target) or target.startswith("#") or target.startswith("mailto:"):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        candidate = (doc.parent / target).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            add(findings, rel(root, doc), "links", f"link escapes repo: {target}", 2)
            continue
        if not candidate.exists():
            add(findings, rel(root, doc), "links", f"broken local link: {target}", 2)


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def check_doc(root: Path, manifest: dict[str, object], path: Path, findings: list[Finding]) -> None:
    rpath = rel(root, path)
    frontmatter, body = read_frontmatter(path)
    for field in REQUIRED_FRONTMATTER:
        if field not in frontmatter or frontmatter[field] in ("", []):
            add(findings, rpath, "frontmatter", f"missing {field}", 3)

    product = manifest.get("product")
    if product and frontmatter.get("product") != product:
        add(findings, rpath, "frontmatter", f"product should be {product}", 2)

    expected_type = DOC_TYPE_BY_PATH.get(path.name)
    if expected_type and frontmatter.get("doc_type") != expected_type:
        add(findings, rpath, "frontmatter", f"doc_type should be {expected_type}", 2)

    paths = frontmatter.get("source_paths")
    if isinstance(paths, str):
        paths = [paths]
    if isinstance(paths, list):
        for source_path in paths:
            if isinstance(source_path, str) and not (root / source_path).exists():
                add(findings, rpath, "source_paths", f"missing source path: {source_path}", 3)

    word_count = len(re.findall(r"\b\w+\b", body))
    if word_count < 180:
        add(findings, rpath, "depth", f"too thin: {word_count} words", 4)

    h2_count = len(re.findall(r"^##\s+", body, flags=re.MULTILINE))
    if h2_count < 3:
        add(findings, rpath, "structure", "needs at least three h2 sections", 2)

    check_links(root, path, body, findings)

    if path.name == "01-architecture.md":
        if "```mermaid" not in body and "```plantuml" not in body:
            add(findings, rpath, "architecture", "missing Mermaid or PlantUML diagram", 8)
        for keyword in ["Context", "Container", "Component"]:
            if keyword.lower() not in body.lower():
                add(findings, rpath, "architecture", f"missing C4 {keyword} view", 4)

    if path.name == "02-directory-structure.md":
        if "```text" not in body and "```" not in body:
            add(findings, rpath, "directory", "missing directory tree code block", 4)
        if "ownership" not in body.lower() and "owner" not in body.lower():
            add(findings, rpath, "directory", "missing ownership boundaries", 3)

    if path.name == "04-requirements.md":
        if not re.search(r"\bREQ-[A-Z0-9-]+", body):
            add(findings, rpath, "requirements", "missing REQ-* identifiers", 8)
        if "acceptance" not in body.lower() and "수용" not in body:
            add(findings, rpath, "requirements", "missing acceptance criteria", 6)

    if path.name == "05-testing-requirements.md":
        if "```bash" not in body:
            add(findings, rpath, "testing", "missing executable test commands", 5)
        for layer in ["unit", "e2e"]:
            if layer not in body.lower():
                add(findings, rpath, "testing", f"missing {layer} layer", 3)

    if path.name == "06-release-process.md":
        if "version" not in body.lower() or "rollback" not in body.lower():
            add(findings, rpath, "release", "must document version and rollback", 5)

    if path.name == "08-ux-evidence.md":
        if not re.search(r"\.(png|jpg|jpeg|gif|webm)", body, flags=re.IGNORECASE):
            add(findings, rpath, "evidence", "missing screenshot/GIF/media reference", 5)


def run(root: Path, min_score: int, json_output: bool) -> int:
    findings: list[Finding] = []
    manifest = read_manifest(root)
    if not manifest:
        add(findings, "hypeproof.docs.yaml", "manifest", "missing manifest", 20)
    else:
        for field in ["product", "version_source", "docs_root", "adr_root"]:
            if not manifest.get(field):
                add(findings, "hypeproof.docs.yaml", "manifest", f"missing {field}", 4)

    current_version = source_version(root, manifest.get("version_source") if manifest else None)
    if not current_version:
        add(findings, "hypeproof.docs.yaml", "version", "cannot resolve version_source", 8)

    for doc in REQUIRED_DEV_DOCS:
        path = root / doc
        if not path.exists():
            add(findings, doc, "required-doc", "missing required dev doc", 10)
            continue
        check_doc(root, manifest, path, findings)

    adr_root = root / "docs/adr"
    if not (adr_root / "README.md").exists():
        add(findings, "docs/adr/README.md", "adr", "missing ADR index", 6)
    if not list(adr_root.glob("000*.md")):
        add(findings, "docs/adr", "adr", "missing at least one numbered ADR", 6)

    if current_version:
        for doc in REQUIRED_DEV_DOCS:
            path = root / doc
            if not path.exists():
                continue
            fm, _ = read_frontmatter(path)
            if fm.get("version") != current_version:
                add(findings, doc, "version", f"frontmatter version must match {current_version}", 3)

    penalty = sum(item.points for item in findings)
    score = max(0, 100 - penalty)
    result = {
        "repo": root.name,
        "git_ref": git_ref(root),
        "score": score,
        "min_score": min_score,
        "findings": [item.as_dict() for item in findings],
    }

    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"docs score: {score}/100 (min {min_score})")
        if findings:
            for item in findings:
                print(f"- [{item.points}] {item.path} {item.check}: {item.message}")
        else:
            print("no findings")

    return 0 if score >= min_score and not findings else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".", help="repository root")
    parser.add_argument("--min-score", type=int, default=95)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    return run(Path(args.repo).resolve(), args.min_score, args.json)


if __name__ == "__main__":
    sys.exit(main())
