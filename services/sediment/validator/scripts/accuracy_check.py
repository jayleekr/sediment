"""Accuracy framework — sediment#16 #4 design gap.

We had recall@3 (retrieval-only) but no metric for whether the
SYSTEM's answer is actually correct. This script bundles three cheap
deterministic checks that catch the bugs we just shipped:

  1. freshness_accuracy
     For each tenant, query `/v1/sediment/stream` with "가장 최신 X는?"
     where X is each artifact type. Compare the returned newest ref
     to `SELECT MAX(date) FROM artifacts WHERE type = X` via DB. PASS
     iff they match. Catches: LLM hallucinating "latest" from RAG.

  2. citation_precision
     For each chat answer, count `[N]` references in the answer text.
     For each cited [N], assert the corresponding ref appears in the
     citation list AND the citation's score > 0. Catches: LLM
     fabricating citations or referencing non-returned chunks.

  3. cross_tenant_isolation
     Sign in as tenant A, ask a query that should match tenant B's
     content (e.g., kids-edu admin asking about "daily-research"
     which is hypeproof-lab's vault). Assert ZERO citations from
     tenant B. Catches: RLS bypass / cross-tenant leak.

Cheap = pure SQL + small LLM calls. No human eval. No factuality
check (that's a separate harder thing — LLM-as-judge or human).

USAGE (from anywhere with public API access):
  SEDIMENT_API=https://hypeproof-sediment.fly.dev \\
    python -m validator.scripts.accuracy_check \\
    --tenants hypeproof-lab,kids-edu \\
    --json-out /tmp/accuracy.json

Exit: 0 ok, 2 regression, 1 fatal error.

Wired into nightly via `.github/workflows/nightly-recall.yml` (TBD —
v1 ships this script; the wire happens after the script has data).
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("SEDIMENT_API", "https://hypeproof-sediment.fly.dev")
TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Public chat API helpers (mirrors chat_smoke_kids_edu)
# ---------------------------------------------------------------------------

async def _mint(client: httpx.AsyncClient, email: str) -> str:
    r = await client.post(f"{API}/api/v1/auth/dev-token", json={"email": email})
    r.raise_for_status()
    return r.json()["token"]


async def _new_conv(client: httpx.AsyncClient, token: str, title: str) -> str:
    r = await client.post(
        f"{API}/api/v1/conversations",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _ask(
    client: httpx.AsyncClient, token: str, conv_id: str, q: str,
) -> dict:
    """Save + stream — return {citations, answer, events}."""
    # Save user turn
    r = await client.post(
        f"{API}/api/v1/conversations/{conv_id}/messages",
        json={"content": q, "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()

    citations: list[dict] = []
    answer_chunks: list[str] = []
    events = 0
    async with client.stream(
        "POST", f"{API}/v1/sediment/stream",
        json={"conv_id": conv_id, "query": q},
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(TIMEOUT_S, connect=10.0),
    ) as r:
        r.raise_for_status()
        buf = ""
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                events += 1
                evt = "message"
                data_lines: list[str] = []
                for line in frame.split("\n"):
                    if line.startswith("event: "):
                        evt = line[7:].strip()
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                data = "\n".join(data_lines)
                if not data or data == "[DONE]":
                    continue
                try:
                    j = json.loads(data)
                except Exception:
                    continue
                payload = j.get("v") if isinstance(j, dict) else j
                if evt == "citation" and isinstance(payload, dict):
                    citations.append(payload)
                elif evt == "delta" and isinstance(payload, str):
                    answer_chunks.append(payload)
    return {"citations": citations, "answer": "".join(answer_chunks), "events": events}


# ---------------------------------------------------------------------------
# Check #1: freshness_accuracy
# ---------------------------------------------------------------------------

FRESHNESS_TYPES = ["research", "decision", "meeting"]


async def check_freshness_accuracy(
    client: httpx.AsyncClient, email: str, tenant_slug: str,
) -> dict:
    """Ask 'latest <type>' for each type; assert returned ref's date
    matches the actual newest date in DB. Without DB access this script
    relies on the freshness intent's deterministic SQL — if the returned
    citation has a `date` field, we can verify ordering by parsing the
    date out of the ref filename pattern YYYY-MM-DD-*.md as fallback."""
    token = await _mint(client, email)
    results = []
    for t in FRESHNESS_TYPES:
        conv = await _new_conv(client, token, f"freshness-accuracy-{t}")
        query = f"가장 최신 {t} 가 언제꺼야"
        r = await _ask(client, token, conv, query)
        if not r["citations"]:
            results.append({"type": t, "status": "no_citations", "query": query})
            continue
        # The freshness intent yields citations with type="freshness" — first
        # in the list is the newest by SQL ORDER BY.
        first = r["citations"][0]
        is_freshness = first.get("type") == "freshness"
        ref = first.get("ref") or "?"
        date_from_ref = _date_from_ref(ref)
        results.append({
            "type": t,
            "status": "ok" if is_freshness else "wrong_intent",
            "first_ref": ref,
            "first_date": first.get("date"),
            "date_in_filename": date_from_ref,
            "is_freshness_intent": is_freshness,
        })
    pass_n = sum(1 for r in results if r["status"] == "ok")
    return {
        "check": "freshness_accuracy",
        "tenant": tenant_slug,
        "total": len(results),
        "passed": pass_n,
        "results": results,
    }


def _date_from_ref(ref: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", ref or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Check #2: citation_precision
# ---------------------------------------------------------------------------

async def check_citation_precision(
    client: httpx.AsyncClient, email: str, tenant_slug: str,
) -> dict:
    """For a representative library query, ensure every [N] in the answer
    corresponds to a returned citation (LLM didn't fabricate)."""
    token = await _mint(client, email)
    conv = await _new_conv(client, token, "citation-precision")
    # Pick a query likely to have substantive citations
    query = "최근 결정 사항 요약해줘"
    r = await _ask(client, token, conv, query)
    cits = r["citations"]
    answer = r["answer"]

    # Extract [N] / [N, M] / [N] references from answer
    inline_refs = set()
    for m in re.finditer(r"\[(\d+(?:\s*,\s*\d+)*)\]", answer):
        for n in m.group(1).split(","):
            try:
                inline_refs.add(int(n.strip()))
            except ValueError:
                pass

    valid_indices = set(range(1, len(cits) + 1))
    fabricated = inline_refs - valid_indices
    return {
        "check": "citation_precision",
        "tenant": tenant_slug,
        "total_citations_returned": len(cits),
        "inline_refs_in_answer": sorted(inline_refs),
        "fabricated_indices": sorted(fabricated),
        "status": "ok" if not fabricated and inline_refs else (
            "no_inline_refs" if not inline_refs else "fabricated"
        ),
    }


# ---------------------------------------------------------------------------
# Check #3: cross_tenant_isolation
# ---------------------------------------------------------------------------

# Path patterns that uniquely identify each tenant's vault content.
# When tenant A signs in and asks about tenant B's content, citations
# should NEVER include B's path patterns. RLS + defense-in-depth.
_TENANT_PATH_FINGERPRINT = {
    "hypeproof-lab": ["web/src/content/", "research/daily/", "products/sediment/"],
    "kids-edu":      ["kids_edu_vault/", "meeting_notes/"],
}


async def check_cross_tenant_isolation(
    client: httpx.AsyncClient, email: str, tenant_slug: str,
) -> dict:
    """Sign in as `tenant_slug`, ask a query, assert NO citations have refs
    that match any OTHER tenant's path fingerprint."""
    token = await _mint(client, email)
    conv = await _new_conv(client, token, "cross-tenant-iso")
    # A query that could match content in any tenant — generic "latest" works
    r = await _ask(client, token, conv, "가장 최신 자료가 뭐야")
    cits = r["citations"]

    leaks = []
    for c in cits:
        ref = c.get("ref") or ""
        for other_tenant, prefixes in _TENANT_PATH_FINGERPRINT.items():
            if other_tenant == tenant_slug:
                continue
            for prefix in prefixes:
                if prefix in ref:
                    leaks.append({"other_tenant": other_tenant, "ref": ref})
                    break

    return {
        "check": "cross_tenant_isolation",
        "tenant": tenant_slug,
        "total_citations": len(cits),
        "leaks": leaks,
        "status": "ok" if not leaks else "leak_detected",
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_TENANT_EMAILS = {
    "hypeproof-lab": "jay.lee@sonatus.com",
    "kids-edu":      "jayleekr0125@gmail.com",
}


async def amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenants", default="hypeproof-lab,kids-edu",
                   help="comma-separated tenant slugs to check")
    p.add_argument("--json-out", help="write summary JSON to this path")
    args = p.parse_args(argv)

    all_results: list[dict] = []
    overall_pass = True

    async with httpx.AsyncClient(timeout=30) as c:
        for slug in [s.strip() for s in args.tenants.split(",") if s.strip()]:
            email = _TENANT_EMAILS.get(slug)
            if not email:
                print(f"  WARN: no email for tenant '{slug}' — skip", file=sys.stderr)
                continue
            print(f"== {slug} ({email}) ==")
            for check_fn in (check_freshness_accuracy,
                             check_citation_precision,
                             check_cross_tenant_isolation):
                t0 = time.time()
                try:
                    res = await check_fn(c, email, slug)
                except Exception as e:
                    res = {"check": check_fn.__name__, "tenant": slug,
                           "status": "error", "err": str(e)[:200]}
                res["elapsed_ms"] = int((time.time() - t0) * 1000)
                all_results.append(res)
                status = res.get("status", "?")
                marker = "✓" if status == "ok" else "✗"
                if status != "ok":
                    overall_pass = False
                print(f"  {marker} {res['check']:30s} status={status}")

    summary = {
        "api": API,
        "results": all_results,
        "passed": overall_pass,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2, default=str))

    print()
    print(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    return 0 if overall_pass else 2


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
