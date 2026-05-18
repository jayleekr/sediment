"""Per-query recall@3 regression script.

Runs all 40 golden queries through /search, prints recall per query and
flags any that fall below 1.0 (full hit). Run via:

  cd services/sediment && .venv/bin/python -m validator.checks.regression_rag

Output mirrors the rubric's recall@k check but exposes per-query detail
that the aggregate score hides. Use this after any retrieval-side change
to catch silent regressions on previously-passing queries.
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validator.checks.lib_rag import _make_token, _run_golden, _recall_at_k  # noqa: E402


async def main():
    token = await _make_token()
    rows, _ = await _run_golden(token)
    total = len(rows)
    full_pass = 0
    partial = 0
    miss = 0
    detail: list[tuple[str, float, str]] = []
    for r in rows:
        rec = _recall_at_k(r["hits"], r["ideal_refs"], k=3)
        if rec >= 1.0:
            full_pass += 1
            verdict = "PASS"
        elif rec > 0:
            partial += 1
            verdict = "PART"
        else:
            miss += 1
            verdict = "MISS"
        detail.append((r["id"], rec, verdict))

    print(f"recall@3: {full_pass}/{total} full pass, {partial} partial, {miss} miss")
    print(f"avg: {sum(d[1] for d in detail)/total*100:.1f}%")
    print()
    print(f"{'id':<8} {'rec':>5}  status  top-3")
    print("-" * 80)
    for r, (qid, rec, verdict) in zip(rows, detail):
        top3 = ", ".join(h.split("/")[-1][:30] for h in r["hits"][:3]) or "(none)"
        print(f"{qid:<8} {rec:>5.2f}  {verdict:<6}  {top3}")

    # Exit code: 0 if no regressions vs reference baseline below.
    # Baseline = the 5 queries that historically failed (GQ-017/020/024/029/035).
    # ANY query OUTSIDE that set with rec < 1.0 = regression.
    baseline_failures = {"GQ-017", "GQ-020", "GQ-024", "GQ-029", "GQ-035"}
    regressions = [qid for qid, rec, _v in detail
                   if rec < 1.0 and qid not in baseline_failures]
    if regressions:
        print()
        print(f"⚠️  REGRESSIONS ({len(regressions)}): {regressions}")
        sys.exit(1)
    print()
    print("✅ no regressions vs historical baseline")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
