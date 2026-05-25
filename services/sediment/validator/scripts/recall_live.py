"""Live-prod recall@3 check — minimal dependencies (httpx + yaml).

Talks to the public sediment API (default https://hypeproof-sediment.fly.dev),
mints a dev token, runs all 40 golden queries through /api/v1/library/search,
and prints per-query recall plus aggregate.

Unlike `validator.checks.regression_rag` (which runs from the same process as
the API and uses internal token minting), this one only needs the public
surface — so it can run from CI without DB credentials.

Usage:
    python -m validator.scripts.recall_live
    SEDIMENT_API=https://other.example python -m validator.scripts.recall_live
    SEDIMENT_EMAIL=alt@member python -m validator.scripts.recall_live

Exit codes:
    0  ok — no regression vs baseline
    1  unrecoverable error (auth fail / network)
    2  recall regression detected (drop in PASS count below threshold)
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

API = os.environ.get("SEDIMENT_API", "https://hypeproof-sediment.fly.dev")
EMAIL = os.environ.get("SEDIMENT_EMAIL", "jayleekr0125@gmail.com")
MIN_PASS = int(os.environ.get("RECALL_MIN_PASS", "20"))  # default = hypeproof-lab baseline

# Default queries file = hypeproof-lab. Override with RECALL_QUERIES env or
# --queries CLI arg for other tenants (e.g. kids-edu).
_GOLDEN = Path(os.environ.get(
    "RECALL_QUERIES",
    str(Path(__file__).resolve().parents[1] / "golden_queries.yaml"),
))


def _recall_at_k(hits, ideal, k):
    if not ideal:
        return 1.0
    topk = hits[:k]
    return sum(1 for ref in ideal if any(ref in h for h in topk)) / len(ideal)


async def _mint_token(client: httpx.AsyncClient) -> str:
    r = await client.post(f"{API}/api/v1/auth/dev-token", json={"email": EMAIL})
    r.raise_for_status()
    return r.json()["token"]


async def _run() -> dict:
    items = yaml.safe_load(_GOLDEN.read_text())["queries"]
    rows = []
    latencies = []
    async with httpx.AsyncClient(timeout=30) as c:
        token = await _mint_token(c)
        for q in items:
            t0 = time.time()
            r = await c.get(
                f"{API}/api/v1/library/search",
                params={"q": q["q"], "limit": 5},
                headers={"Authorization": f"Bearer {token}"},
            )
            elapsed = int((time.time() - t0) * 1000)
            latencies.append(elapsed)
            if r.status_code != 200:
                rows.append({"id": q["id"], "hits": [],
                             "ideal_refs": q.get("ideal_refs", []),
                             "lat_ms": elapsed, "err": r.status_code})
                continue
            refs = [it["ref"] for it in r.json().get("items", [])]
            rows.append({"id": q["id"], "hits": refs,
                         "ideal_refs": q.get("ideal_refs", []),
                         "lat_ms": elapsed})
    return {"rows": rows, "latencies": latencies}


def _format(rep: dict) -> tuple[str, int]:
    rows = rep["rows"]
    latencies = rep["latencies"]
    total = len(rows)
    detail = []
    pass_n = part_n = miss_n = 0
    for r in rows:
        rec = _recall_at_k(r["hits"], r["ideal_refs"], 3)
        if rec >= 1.0:
            verdict, pass_n = "PASS", pass_n + 1
        elif rec > 0:
            verdict, part_n = "PART", part_n + 1
        else:
            verdict, miss_n = "MISS", miss_n + 1
        detail.append((r["id"], rec, verdict, r["lat_ms"], r.get("err")))

    avg = sum(d[1] for d in detail) / total * 100
    lats = sorted(latencies)
    p50 = lats[len(lats) // 2]
    p95 = lats[int(len(lats) * 0.95)]

    lines = [
        f"=== recall@3 against {API} ===",
        f"total {total} | PASS {pass_n} | PART {part_n} | MISS {miss_n}",
        f"avg recall: {avg:.1f}%",
        f"latency p50/p95: {p50}/{p95} ms",
        "",
        f"{'id':<8} {'rec':>5} {'lat':>7}  {'status':<6}  top-3",
        "-" * 90,
    ]
    for qid, rec, verdict, lat, err in detail:
        r = next(x for x in rows if x["id"] == qid)
        top3 = ", ".join(h.split("/")[-1][:30] for h in r["hits"][:3]) or "(none)"
        extra = f" [HTTP {err}]" if err else ""
        lines.append(f"{qid:<8} {rec:>5.2f} {lat:>5}ms  {verdict:<6}  {top3}{extra}")

    return "\n".join(lines), pass_n


async def main():
    try:
        rep = await _run()
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    text, pass_n = _format(rep)
    print(text)

    # Write machine-readable summary for CI consumption.
    summary = {
        "api": API,
        "total": len(rep["rows"]),
        "pass_n": pass_n,
        "threshold": MIN_PASS,
        "details": [
            {"id": r["id"],
             "recall_at_3": _recall_at_k(r["hits"], r["ideal_refs"], 3),
             "lat_ms": r["lat_ms"]}
            for r in rep["rows"]
        ],
    }
    if os.environ.get("RECALL_JSON_OUT"):
        Path(os.environ["RECALL_JSON_OUT"]).write_text(json.dumps(summary, indent=2))

    if pass_n < MIN_PASS:
        print(f"\nFAIL: {pass_n} PASS < threshold {MIN_PASS}", file=sys.stderr)
        sys.exit(2)
    print(f"\nOK: {pass_n}/{len(rep['rows'])} pass ≥ threshold {MIN_PASS}")


if __name__ == "__main__":
    asyncio.run(main())
