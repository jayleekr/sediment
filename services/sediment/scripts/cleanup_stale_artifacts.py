"""Cleanup stale-path artifacts (sediment#16 fix #3).

Background: the hypeprooflab vault was reorganized at some point — research
files moved from `research/daily/*.md` to `web/src/content/research/ko/*.md`,
columns from `columns/*.md` to `web/src/content/columns/ko/*.md`, etc. The
vault-ingest webhook handled the ADDS but RENAMED-as-DELETE-plus-ADD didn't
fully wire, so OLD-path artifacts accumulate. They show up in retrieval as
duplicate citations.

This script:
  1. Identifies pairs of (old_path_artifact, new_path_artifact) with matching
     stem (date + slug part).
  2. DELETEs the old-path artifact. ON DELETE CASCADE removes chunks.
  3. Writes an audit_log row so the deletion is traceable.

Idempotent: re-running after cleanup is a no-op.

USAGE:
  # Inside the Fly VM (service role has BYPASSRLS for admin ops):
  python -m scripts.cleanup_stale_artifacts --tenant hypeproof-lab --dry-run
  python -m scripts.cleanup_stale_artifacts --tenant hypeproof-lab    # do it

  # All tenants:
  python -m scripts.cleanup_stale_artifacts --all
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[1]))

from sqlalchemy import text  # noqa: E402

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("cleanup_stale")


# Mapping rules: (old_path_glob, new_path_glob, stem_extractor)
# Each pair declares: an old path family that's been superseded by a new
# path family. The stem extractor pulls the comparable identifier (date or
# slug) out of each ref. When the same stem appears in both families for
# the same tenant, the OLD artifact is deletable.
#
# Add new pairs here as further vault refactors happen.
_RULES = [
    {
        "name": "research_daily_to_webcontent",
        "old_like": "research/daily/%-ko.md",
        "new_like": "web/src/content/research/ko/%.md",
        "old_re": r"^research/daily/(.+)-ko\.md$",
        "new_re": r"^web/src/content/research/ko/(.+)\.md$",
    },
    {
        "name": "research_daily_to_webcontent_en",
        "old_like": "research/daily/en/%.md",
        "new_like": "web/src/content/research/en/%.md",
        "old_re": r"^research/daily/en/(.+)\.md$",
        "new_re": r"^web/src/content/research/en/(.+)\.md$",
    },
]


async def _find_stale_pairs(
    tenant_slug: str | None, rule: dict
) -> list[tuple[str, str, str]]:
    """Return [(old_ref, new_ref, tenant_slug), ...] pairs to clean up."""
    where_tenant = ""
    params = {
        "old_like": rule["old_like"],
        "new_like": rule["new_like"],
        "old_re": rule["old_re"],
        "new_re": rule["new_re"],
    }
    if tenant_slug:
        where_tenant = "AND t.slug = :slug"
        params["slug"] = tenant_slug

    sql = f"""
        SELECT a.ref AS old_ref, b.ref AS new_ref, t.slug
        FROM artifacts a
        JOIN artifacts b ON a.tenant_id = b.tenant_id
        JOIN tenants t ON t.id = a.tenant_id
        WHERE a.ref LIKE :old_like
          AND b.ref LIKE :new_like
          AND regexp_replace(a.ref, :old_re, '\\1') =
              regexp_replace(b.ref, :new_re, '\\1')
          {where_tenant}
        ORDER BY t.slug, a.ref
    """
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        return [(row[0], row[1], row[2]) for row in r]


async def _delete_artifact(tenant_slug: str, ref: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    async with service_session() as s:
        r = await s.execute(text("""
            DELETE FROM artifacts
            WHERE id IN (
                SELECT a.id FROM artifacts a
                JOIN tenants t ON t.id = a.tenant_id
                WHERE t.slug = :slug AND a.ref = :ref
            )
        """), {"slug": tenant_slug, "ref": ref})
        # audit row — service_session is BYPASSRLS so we can write to any tenant
        await s.execute(text("""
            INSERT INTO audit_log (action, payload)
            VALUES ('stale_artifact.deleted',
                    jsonb_build_object('tenant_slug', :slug, 'ref', :ref))
        """), {"slug": tenant_slug, "ref": ref})
        await s.commit()
        return r.rowcount > 0


async def amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="tenant slug; omit for --all")
    g.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    tenant_slug = None if args.all else args.tenant
    total_pairs = 0
    deleted = 0
    summary: list[dict] = []

    for rule in _RULES:
        log.info("cleanup.rule.start", rule=rule["name"], dry_run=args.dry_run)
        pairs = await _find_stale_pairs(tenant_slug, rule)
        total_pairs += len(pairs)
        log.info("cleanup.rule.found_pairs", rule=rule["name"], count=len(pairs))
        for old_ref, new_ref, slug in pairs:
            ok = await _delete_artifact(slug, old_ref, args.dry_run)
            if ok:
                deleted += 1
            summary.append({"rule": rule["name"], "tenant": slug,
                            "deleted": old_ref, "superseded_by": new_ref,
                            "dry_run": args.dry_run})

    log.info("cleanup.done",
             total_pairs=total_pairs,
             actually_deleted=(0 if args.dry_run else deleted),
             dry_run=args.dry_run)
    print(json.dumps({
        "total_pairs": total_pairs,
        "deleted": (0 if args.dry_run else deleted),
        "dry_run": args.dry_run,
        "samples": summary[:10],
    }, indent=2))
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
