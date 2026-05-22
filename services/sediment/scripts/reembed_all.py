"""One-shot re-embed: replace zero-vector chunks with real Gemini embeddings.

Background: per sediment#16, all 7082 prod chunks have zero-vector
embeddings because the previous OpenAI-only embedder fell back silently
when OPENAI_API_KEY was unset. After switching to Gemini (default
provider as of 2026-05-22), this script walks every chunk and
re-embeds its content.

Safe to re-run — idempotent. Skips chunks whose vector is already
non-zero (so partial runs resume cleanly).

USAGE (in prod VM):
  python -m scripts.reembed_all                      # all tenants, all chunks
  python -m scripts.reembed_all --tenant kids-edu    # one tenant
  python -m scripts.reembed_all --limit 100          # cap (testing)
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[1]))

from sqlalchemy import text  # noqa: E402

from lab_lib.db import service_session  # noqa: E402
from lab_lib.embeddings import embed, EMBEDDING_DIM  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("reembed")


def _vec_to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


_ZERO = "[" + ",".join("0" for _ in range(EMBEDDING_DIM)) + "]"


async def _fetch_zero_chunks(tenant_slug: str | None, limit: int, batch: int) -> list[dict]:
    where_tenant = ""
    params = {"z": _ZERO, "lim": batch}
    if tenant_slug:
        where_tenant = "AND t.slug = :slug"
        params["slug"] = tenant_slug
    sql = f"""
        SELECT c.id::text AS cid, c.content
        FROM chunks c
        JOIN tenants t ON t.id = c.tenant_id
        WHERE c.embedding = CAST(:z AS vector) {where_tenant}
        LIMIT :lim
    """
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        return [{"cid": row[0], "content": row[1] or " "} for row in r]


async def _update_vecs(rows: list[dict], vecs: list[list[float]]) -> int:
    if not rows:
        return 0
    async with service_session() as s:
        # Single transaction, one UPDATE per row. asyncpg batches inside the
        # transaction; ~50 rows/sec is plenty.
        for row, vec in zip(rows, vecs):
            await s.execute(text("""
                UPDATE chunks SET embedding = CAST(:v AS vector)
                WHERE id = CAST(:cid AS uuid)
            """), {"v": _vec_to_pg(vec), "cid": row["cid"]})
        await s.commit()
    return len(rows)


async def amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", help="tenant slug (omit for all)")
    p.add_argument("--limit", type=int, default=0, help="total cap (0 = unlimited)")
    p.add_argument("--batch", type=int, default=64)
    args = p.parse_args(argv)

    total = 0
    while True:
        remaining = (args.limit - total) if args.limit else args.batch
        if remaining <= 0:
            break
        fetch = min(remaining, args.batch)
        rows = await _fetch_zero_chunks(args.tenant, args.limit or 1_000_000, fetch)
        if not rows:
            log.info("reembed.done", total=total)
            break

        texts = [r["content"] for r in rows]
        try:
            vecs = embed(texts)
        except Exception as e:
            log.error("reembed.embed_failed", err=str(e)[:200])
            return 2

        # Sanity: ensure vectors are non-zero before writing
        non_zero = sum(1 for v in vecs if any(abs(x) > 1e-9 for x in v))
        if non_zero == 0:
            log.error("reembed.still_zero", n=len(vecs),
                      hint="EMBEDDING_PROVIDER or GEMINI_API_KEY misconfigured")
            return 3

        n = await _update_vecs(rows, vecs)
        total += n
        log.info("reembed.batch", batch=n, total=total, non_zero_in_batch=non_zero)

    print(f"OK reembedded {total} chunks")
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
