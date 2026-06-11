#!/usr/bin/env python3
"""Bootstrap the validation harness into a new project.

Usage:
  python harness/bootstrap.py \\
    --target  /path/to/new-project/services/myapp/validator \\
    --project myapp \\
    --postgres-container myapp-pg \\
    --service-ports 10100,10020,11000,12000

What it does:
  1. Copies harness Python modules + check libraries to <target>/
  2. Generates rubric.yaml / recipes.yaml / e2e_spec.yaml from skeletons
     (templated with project name + container + ports)
  3. Creates an empty checks/ directory + __init__.py
  4. Prints next steps:
     - copy .claude/agents/sediment-*.md to new project's .claude/agents/
     - copy .claude/skills/sediment-validate/ to new project's .claude/skills/
     - edit rubric.yaml to add your project's checks
     - run `make validate-p0` once infra is up
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

# Resolve harness root (this file lives in <project>/harness/)
HARNESS_ROOT = Path(__file__).resolve().parent
SOURCE_VALIDATOR = HARNESS_ROOT.parent / "services" / "sediment" / "validator"
TEMPLATES = HARNESS_ROOT / "templates"

# Files that are 100% reusable across projects (verbatim copy).
GENERIC_FILES = [
    "__init__.py",
    "__main__.py",
    "types.py",
    "report.py",
    "dispatch.py",
    "runner.py",
    "loop.py",
    "fixer.py",
    "e2e_runner.py",
]

GENERIC_CHECK_LIBS = [
    "checks/__init__.py",
    "checks/lib_rls.py",
    "checks/lib_rag.py",
    "checks/lib_security.py",
]


def render(template_path: Path, replacements: dict[str, str]) -> str:
    text = template_path.read_text()
    for key, val in replacements.items():
        text = text.replace("{" + key + "}", val).replace("{{" + key + "}}", val)
    return text


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap validation harness")
    p.add_argument("--target", type=Path, required=True,
                    help="destination dir, e.g., services/myapp/validator")
    p.add_argument("--project", required=True, help="short project slug, e.g., myapp")
    p.add_argument("--postgres-container", default=None,
                    help="docker container name for Postgres (default: <project>-pg)")
    p.add_argument("--service-ports", default="10100,10020,11000,12000",
                    help="comma-separated, must be 4 ports (platform,langgraph,ingester,metadata)")
    p.add_argument("--force", action="store_true",
                    help="overwrite target if exists")
    args = p.parse_args()

    target: Path = args.target.resolve()
    if target.exists() and not args.force:
        print(f"target exists: {target}\n  pass --force to overwrite", file=sys.stderr)
        return 1

    target.mkdir(parents=True, exist_ok=True)
    (target / "checks").mkdir(exist_ok=True)

    # 1. Copy generic Python modules
    print(f"[1/4] copying generic modules to {target}")
    for rel in GENERIC_FILES:
        src = SOURCE_VALIDATOR / rel
        if not src.exists():
            print(f"  WARN: source missing: {src}", file=sys.stderr)
            continue
        dst = target / rel
        shutil.copy2(src, dst)
        print(f"   ✓ {rel}")

    for rel in GENERIC_CHECK_LIBS:
        src = SOURCE_VALIDATOR / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dst)
            print(f"   ✓ {rel}")

    # 2. Render skeleton configs
    print(f"[2/4] rendering skeleton configs")
    pg_container = args.postgres_container or f"{args.project}-pg"
    ports = args.service_ports.split(",")
    while len(ports) < 4:
        ports.append("10000")
    replacements = {
        "PROJECT_NAME": args.project.title(),
        "project": args.project,
        "postgres-container": pg_container,
        "port": ports[0],
    }

    for skel_name, out_name in [
        ("rubric.skeleton.yaml", "rubric.yaml"),
        ("recipes.skeleton.yaml", "recipes.yaml"),
        ("e2e_spec.skeleton.yaml", "e2e_spec.yaml"),
    ]:
        skel = TEMPLATES / skel_name
        out = target / out_name
        if out.exists() and not args.force:
            print(f"   ! skipping {out_name} (exists)")
            continue
        out.write_text(render(skel, replacements))
        print(f"   ✓ {out_name}")

    # 3. Create empty per-phase check stub
    print("[3/4] creating per-phase check stub")
    stub = target / "checks" / "p0_scaffolding.py"
    if not stub.exists() or args.force:
        stub.write_text(f'''"""Phase 0 checks for {args.project}. Customize per project."""
from __future__ import annotations
from sqlalchemy import text

# Replace with your tenant-scoped table list.
TENANT_TABLES = [
    "tenants", "members", "artifacts",
    # ...
]


async def check_all_tables_exist(spec: dict, **_) -> dict:
    """Stub — implement using lab_lib.db.service_session."""
    return {{"passed": False, "message": "TODO: implement"}}


async def check_rls_enabled_all(spec: dict, **_) -> dict:
    """Stub — implement."""
    return {{"passed": False, "message": "TODO: implement"}}
''')
        print("   ✓ checks/p0_scaffolding.py (stub)")

    # 4. Print next steps
    print("[4/4] next steps:")
    print()
    print("  1. cp ../../harness/MANIFEST.md  ./harness/MANIFEST.md")
    print("  2. Copy agent files to your project:")
    print(f"       cp <hypeproof-repo>/.claude/agents/sediment-*.md  <new-project>/.claude/agents/")
    print(f"  3. Copy slash command:")
    print(f"       cp -R <hypeproof-repo>/.claude/skills/sediment-validate  <new-project>/.claude/skills/")
    print(f"  4. Edit rubric.yaml to add your phase checks (start small — 5 checks)")
    print(f"  5. Run: cd {target.parent.parent} && python -m validator --phase P0")
    print()
    print(f"  Generic harness installed at: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
