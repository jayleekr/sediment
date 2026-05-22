"""One-shot historical cleanup of test-pollution conversations.

After Jay surfaced the sidebar full of `kids-edu-smoke`, `probe`,
`freshness-accuracy-*` etc. (UX critique 2026-05-22). Runs once in prod
to scrub the existing pile; the new `list_convs` filter prevents future
pollution from being visible (design 15 §7).

USAGE:
  python -m scripts.cleanup_test_conversations --dry-run    # report
  python -m scripts.cleanup_test_conversations              # do it

Idempotent. Logs per-pattern counts.
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
log = get_logger("cleanup_test_conv")


# Same list as conversations router's filter (kept in sync — both files).
_TEST_TITLE_PATTERNS = [
    "sec-check", "lab-priv", "rls-check",
    "kids-edu-smoke", "freshness-accuracy-",
    "citation-precision", "cross-tenant-iso",
    "discord-reply-",
    "probe", "probe2", "test:",
    "openclaw-",
]


async def amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    where_clause = " OR ".join(
        f"title ILIKE '{p_}%'" for p_ in _TEST_TITLE_PATTERNS
    )

    async with service_session() as s:
        # Count first
        r = await s.execute(text(f"""
            SELECT t.slug, count(*)
            FROM conversations c
            JOIN tenants t ON t.id = c.tenant_id
            WHERE ({where_clause})
            GROUP BY t.slug
            ORDER BY t.slug
        """))
        by_tenant = {row[0]: row[1] for row in r}
        total = sum(by_tenant.values())

        log.info("cleanup.found", total=total, by_tenant=by_tenant,
                 patterns=_TEST_TITLE_PATTERNS)

        if args.dry_run:
            print(json.dumps({"dry_run": True, "found": by_tenant,
                              "total": total}, indent=2))
            return 0

        # Delete (cascade to messages via FK)
        await s.execute(text(f"""
            DELETE FROM conversations
            WHERE ({where_clause})
        """))
        await s.commit()
        log.info("cleanup.done", deleted=total)
        print(json.dumps({"dry_run": False, "deleted": by_tenant,
                          "total": total}, indent=2))
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
