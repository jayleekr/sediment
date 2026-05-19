"""Walk the repo and ingest existing markdown into the default tenant.

Calls vault-ingester HTTP API, so the ingester must be running:
    make ingester

Run: make ingest
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
import frontmatter
import httpx

SCRIPT = Path(__file__).resolve()


def _vault_root() -> Path:
    """The checkout that holds the VAULT CONTENT (research/, web/src/content/,
    novels/, …). Post repo-split that is `jayleekr/hypeprooflab`, NOT this
    product repo — so it must be given explicitly. The old hardcoded
    parents[5] assumed the pre-split monorepo and is wrong here.
    """
    env = os.environ.get("SEDIMENT_VAULT_ROOT")
    if env:
        return Path(env).resolve()
    # Back-compat: still inside a monorepo that has research/daily? use it.
    for p in SCRIPT.parents:
        if (p / "research" / "daily").is_dir() or (p / "web" / "src" / "content").is_dir():
            return p
    raise SystemExit(
        "SEDIMENT_VAULT_ROOT unset and no vault content found above "
        f"{SCRIPT}. Point it at a hypeprooflab checkout: "
        "SEDIMENT_VAULT_ROOT=/path/to/hypeprooflab"
    )


REPO_ROOT = _vault_root()
sys.path.insert(0, str(SCRIPT.parents[1]))

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402
from lab_lib.settings import settings  # noqa: E402
from sqlalchemy import text  # noqa: E402

configure_logging()
log = get_logger("ingest_repo")

INGESTER_URL = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"

# Paths (relative to REPO_ROOT) to exclude from ingest — internal/generated dirs
# that would pollute user-facing search results.
_INGEST_EXCLUDE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("products", "ai-curator", "output"),
    ("products", "ai-curator", "harness"),
)

# Filename prefixes (basename only) to exclude regardless of directory.
_INGEST_EXCLUDE_BASENAMES: frozenset[str] = frozenset([
    "SESSION_",
    "RALPH_",
])


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    for pfx in _INGEST_EXCLUDE_PREFIXES:
        if rel_parts[: len(pfx)] == pfx:
            return True
    basename = rel_parts[-1] if rel_parts else ""
    if any(basename.startswith(prefix) for prefix in _INGEST_EXCLUDE_BASENAMES):
        return True
    return False


# Map directory → artifact type
TYPE_MAP = {
    "research/daily": "research",
    "research/columns": "research",
    "web/src/content/columns/ko": "column",
    "web/src/content/columns/en": "column",
    "web/src/content/research": "research",
    "web/src/content/novels": "novel",
    "novels": "novel",
    "products": "note",
    "docs": "note",
}


def _detect_type(rel: str) -> str:
    for prefix, t in TYPE_MAP.items():
        if rel.startswith(prefix):
            return t
    return "note"


def _author_external_id(fm: dict, members_by_name: dict[str, str]) -> str | None:
    a = fm.get("author") or fm.get("creator")
    if isinstance(a, str):
        return members_by_name.get(a)
    return None


async def gather_files() -> list[Path]:
    out: list[Path] = []
    paths = settings.vault_ingest_paths.split(",")
    for p in paths:
        p = p.strip()
        full = REPO_ROOT / p
        if not full.exists():
            log.warning("ingest.path.missing", path=str(full))
            continue
        if full.is_file():
            if full.suffix == ".md":
                out.append(full)
            continue
        for md in full.rglob("*.md"):
            # Filter only relative-to-REPO_ROOT parts. Absolute paths may include
            # ".claude" (worktree dir) which would falsely filter everything.
            try:
                rel_parts = md.relative_to(REPO_ROOT).parts
            except ValueError:
                rel_parts = md.parts
            if any(part.startswith(".") or part == "node_modules" for part in rel_parts):
                continue
            if _is_excluded(rel_parts):
                continue
            out.append(md)
    return out


async def get_default_tenant_and_members():
    async with service_session() as s:
        r = await s.execute(text("SELECT id FROM tenants WHERE slug = :slug"),
                            {"slug": settings.default_tenant_slug})
        row = r.first()
        if not row:
            raise RuntimeError("default tenant not seeded — run `make seed`")
        tid = str(row[0])
        r = await s.execute(text("SELECT external_id, display_name FROM members WHERE tenant_id = :tid"),
                            {"tid": tid})
        members_by_name: dict[str, str] = {}
        for ext_id, name in r:
            if ext_id:
                members_by_name[name] = ext_id
    return tid, members_by_name


async def ingest_one(client: httpx.AsyncClient, tid: str, file: Path,
                     members_by_name: dict[str, str], sem: asyncio.Semaphore):
    rel = file.relative_to(REPO_ROOT).as_posix()
    try:
        text_body = file.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("ingest.read.fail", file=rel, err=str(e))
        return
    try:
        fm = frontmatter.loads(text_body)
        fmdict = dict(fm.metadata)
    except Exception as e:
        log.warning("ingest.frontmatter.fail", file=rel, err=str(e))
        return
    payload = {
        "tenant_id": tid,
        "ref": rel,
        "type": _detect_type(rel),
        "body": text_body,
        "author_external_id": _author_external_id(fmdict, members_by_name),
    }
    async with sem:
        try:
            r = await client.post(INGESTER_URL, json=payload, timeout=120)
            if r.status_code == 200:
                d = r.json()
                log.info("ingest.ok", ref=rel, chunks=d["chunks_written"], ms=d["elapsed_ms"])
            else:
                log.warning("ingest.fail", ref=rel, status=r.status_code, body=r.text[:200])
        except Exception as e:
            log.exception("ingest.error", ref=rel)


async def main():
    tid, members = await get_default_tenant_and_members()
    log.info("ingest.start", tenant_id=tid, members=len(members))
    files = await gather_files()
    log.info("ingest.files", count=len(files))
    sem = asyncio.Semaphore(int(os.environ.get("INGEST_CONCURRENCY", "3")))
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[ingest_one(client, tid, f, members, sem) for f in files])
    log.info("ingest.done", count=len(files))


if __name__ == "__main__":
    asyncio.run(main())
