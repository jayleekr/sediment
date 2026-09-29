#!/usr/bin/env python3
"""Install only the PR skill/entrypoint bundle into explicitly selected consumers."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
# Consumers that vendor this skill. Keep in step with tests/consumers.txt:
# sync.sh delivers the skill files, this installer delivers the entrypoint
# block, the .agents alias and the SKILLS.md row. A repo in one list but not
# the other is left half-installed (#210).
CONSUMER_REMOTE = re.compile(
    r"(?:https://github\.com/|git@github\.com:)(?:jayleekr)/"
    r"(?:hypeprooflab|hypeproof-studio|sediment)(?:\.git)?"
)
START, END = "<!-- hype-pr-skill:start -->", "<!-- hype-pr-skill:end -->"
BLOCK = f"""{START}
## Agent PR preparation

For development through PR creation in this repository, read and use
`.claude/skills/hype-pr/SKILL.md` (also discoverable at `.agents/skills/hype-pr/`).
Inspect criteria links at task start; before creating a PR, record the agent's
impact assessment and validation with `scripts/hype-pr/pr.py inspect` / `prepare`.
Create through `scripts/hype-pr/pr.py create --preparation <receipt> --apply`.
Fix stale/missing preparation instead of bypassing it with direct `gh pr create`.
The common contract is `docs/AGENT-GUIDE.ko.md`; commands are in `docs/HYPE-PR.ko.md`.
This adds no GitHub required check and does not grant human approval or merge authority.
{END}
"""


def install(target):
    target = Path(target).resolve()
    remote = subprocess.check_output(["git", "-C", str(target), "remote", "get-url", "origin"], text=True).strip()
    if not CONSUMER_REMOTE.fullmatch(remote):
        raise ValueError("installer is scoped to explicit Lab/Studio/Sediment checkouts")
    # Validate the whole destination before writing any managed file.
    destinations = [".claude/skills/hype-pr", "scripts/hype-pr", ".agents/skills/hype-pr",
                    "docs/AGENT-GUIDE.ko.md", "docs/HYPE-PR.ko.md", "AGENTS.md", "CLAUDE.md", ".claude/SKILLS.md"]
    for relative in destinations:
        path = target / relative
        if not path.resolve().is_relative_to(target):
            raise ValueError("managed destination escapes the selected checkout")
    alias = target / ".agents/skills/hype-pr"
    if alias.is_symlink() and alias.readlink().as_posix() != "../../.claude/skills/hype-pr":
        raise ValueError("existing skill alias points elsewhere")
    if alias.exists() and not alias.is_symlink() and not (alias / "HARNESS_VERSION").is_file():
        raise ValueError("existing skill alias is not managed")
    sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    for source, destination in [(ROOT / "skills/hype-pr", target / ".claude/skills/hype-pr"),
                                (ROOT / "scripts/hype-pr", target / "scripts/hype-pr")]:
        if destination.is_symlink():
            raise ValueError(f"refusing to overwrite symlink: {destination}")
        # Preserve consumer-only files; the installed entrypoint delegates to Harness.
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (destination / "HARNESS_VERSION").write_text(sha + "\n")
    # Consumer diff scanners inspect regular files; directory symlinks are not
    # files and are correctly rejected as unscanned. Keep both discovery trees
    # byte-identical through this installer instead of weakening those scanners.
    if alias.is_symlink():
        alias.unlink()
    shutil.copytree(ROOT / "skills/hype-pr", alias, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (alias / "HARNESS_VERSION").write_text(sha + "\n")
    for name in ("AGENT-GUIDE.ko.md", "HYPE-PR.ko.md"):
        (target / "docs").mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "docs" / name, target / "docs" / name)
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = target / name
        text = path.read_text() if path.exists() else f"# {target.name} agent entrypoint\n"
        if START in text or END in text:
            if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
                raise ValueError("ambiguous agent entrypoint block")
            text = text[:text.index(START)] + BLOCK + text[text.index(END) + len(END):].lstrip("\n")
        else:
            text = text.rstrip() + "\n\n" + BLOCK
        path.write_text(text)
    index = target / ".claude/SKILLS.md"
    if index.exists() and not re.search(r"^\|\s*hype-pr\s*\|", index.read_text(), re.M):
        text = index.read_text()
        title, rest = text.split("\n", 1)
        index.write_text(title + "\n\n## PR preparation\n\n| Skill | Command | Description |\n|---|---|---|\n| hype-pr | `/hype-pr` | Criteria links, impact assessment and validated PR creation |\n" + rest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", help="Explicit isolated Lab, Studio or Sediment checkout; never scans siblings")
    install(parser.parse_args().checkout)
