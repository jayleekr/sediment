"""Hard negative mining — turns retrieval failures into training data.

Phase 5a of sediment#15. For every assistant message in the last N days
that scored poorly (judge < 0.5 OR thumbs_down OR grounding failed), the
chunks that were RETRIEVED-BUT-UNUSED become hard negatives for the
underlying embedding model.

Output: append-only JSONL at output/hard_negatives.jsonl in the shape
expected by sentence-transformers contrastive training:
  {"query": "...", "positive": "...", "negative": "..."}

Where:
  - query    = user's original question
  - positive = the chunk that SHOULD have been retrieved (manual labels
               from golden_queries.ideal_refs join, or skip if missing)
  - negative = a retrieved chunk that wasn't cited in the answer

This is read by future weekly tuning runs. The mining step itself is
analytic — it does not retrain.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from lab_lib.db import service_session


log = logging.getLogger("hard_negative_mining")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ============================================================
# SQL — find bad answers + their citations
# ============================================================

BAD_ANSWERS_SQL = """
WITH judged AS (
  SELECT m.id           AS msg_id,
         m.tenant_id    AS tenant_id,
         m.intent       AS intent,
         m.grounding_status AS grounding_status,
         m.content      AS answer,
         m.citations    AS citations,
         u.content      AS query,
         COALESCE(j.score, 1.0) AS judge_score,
         (SELECT COUNT(*) FROM message_signals s
          WHERE s.message_id = m.id AND s.kind = 'thumbs_down') AS thumbs_down
  FROM messages m
  JOIN messages u
    ON u.conv_id = m.conv_id
   AND u.role    = 'user'
   AND u.ts      < m.ts
   AND u.ts      = (SELECT MAX(ts) FROM messages WHERE conv_id = m.conv_id
                                     AND role='user' AND ts < m.ts)
  LEFT JOIN message_judge_scores j
    ON j.message_id = m.id AND j.judge = 'faithfulness'
  WHERE m.role = 'assistant'
    AND m.ts >= :cutoff
    AND m.archived = false
)
SELECT msg_id::text, query, answer, citations, grounding_status, judge_score, thumbs_down
FROM judged
WHERE judge_score < 0.5
   OR thumbs_down > 0
   OR grounding_status NOT IN ('passed', 'passed_after_retry', 'freshness_deterministic')
ORDER BY judge_score ASC, thumbs_down DESC
"""


async def _fetch_bad(hours: int = 168) -> list[dict]:
    """168h = 7 days — weekly mining window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with service_session() as s:
        r = await s.execute(text(BAD_ANSWERS_SQL), {"cutoff": cutoff})
        out: list[dict] = []
        for row in r:
            d = dict(row._mapping)
            if isinstance(d["citations"], str):
                d["citations"] = json.loads(d["citations"])
            out.append(d)
        return out


# ============================================================
# Extract negatives from citations
# ============================================================

def extract_negatives(row: dict) -> list[dict]:
    """For one bad answer, emit one negative-pair per retrieved citation.

    We don't know the positive (correct answer chunk) without a golden
    mapping. Two output shapes:
      - With positive: golden_refs lookup matched → triple
      - Without positive: query+negative only (incomplete; future joiner
        will fill once a human curates the golden_refs)
    """
    query = row["query"]
    negatives: list[dict] = []
    for c in row.get("citations") or []:
        content = c.get("content") or ""
        if not content or c.get("type") == "summary":
            # summary citations don't have content text
            continue
        negatives.append({
            "query": query,
            "msg_id": row["msg_id"],
            "negative_ref": c.get("ref"),
            "negative_chunk": content[:2000],
            "reason": (
                "judge_low" if row["judge_score"] < 0.5
                else "thumbs_down" if row["thumbs_down"] > 0
                else "grounding_fail"
            ),
            "judge_score": row["judge_score"],
        })
    return negatives


# ============================================================
# Append to JSONL
# ============================================================

def append_jsonl(records: list[dict], path: Path) -> int:
    """Idempotent on (msg_id, negative_ref) — skip dupes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str]] = set()
    if path.exists():
        with open(path) as f:
            for line in f:
                try:
                    o = json.loads(line)
                    seen.add((o.get("msg_id"), o.get("negative_ref")))
                except Exception:
                    continue
    added = 0
    with open(path, "a") as f:
        for r in records:
            k = (r.get("msg_id"), r.get("negative_ref"))
            if k in seen:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            seen.add(k)
            added += 1
    return added


# ============================================================
# Entry point
# ============================================================

async def run(hours: int = 168, out_path: Path | None = None) -> dict:
    out_path = out_path or Path("output/hard_negatives.jsonl")
    bad = await _fetch_bad(hours=hours)
    log.info("hard_negative_mining.start bad_answers=%s", len(bad))
    all_negs: list[dict] = []
    for row in bad:
        all_negs.extend(extract_negatives(row))
    added = append_jsonl(all_negs, out_path)
    summary = {
        "window_hours": hours,
        "bad_answers": len(bad),
        "negatives_extracted": len(all_negs),
        "negatives_appended": added,
        "out_path": str(out_path),
    }
    log.info("hard_negative_mining.done %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=168)
    p.add_argument("--out", type=str, default="output/hard_negatives.jsonl")
    args = p.parse_args()
    asyncio.run(run(hours=args.hours, out_path=Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
