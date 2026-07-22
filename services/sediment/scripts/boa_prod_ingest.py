"""Ingest BOA generated markdown into deployed Sediment.

Strict rules:
- requires an explicit prod JWT via SEDIMENT_TOKEN;
- ingests only refs listed in assets/generated/boa_manifest.json;
- fails on the first API error;
- writes an audit report under assets/generated/boa_reports.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.boa_lib import BOA_MANIFEST_PATH, BoaError, parse_profile_arg, write_report  # noqa: E402


async def _whoami(client: httpx.AsyncClient, base_url: str, token: str) -> dict[str, Any]:
    r = await client.get(
        f"{base_url}/api/v1/auth/whoami",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if r.status_code != 200:
        raise BoaError(f"whoami failed {r.status_code}: {r.text[:300]}")
    payload = r.json()
    if payload.get("role") not in {"admin", "creator"}:
        raise BoaError(f"token role cannot ingest: {payload.get('role')}")
    return payload


async def _ingest_one(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    ref: str,
    body: str,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    r = await client.post(
        f"{base_url}/api/v1/ingest/document",
        headers={"Authorization": f"Bearer {token}"},
        json={"ref": ref, "type": "note", "body": body},
        timeout=180,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code != 200:
        raise BoaError(f"ingest failed for {ref}: {r.status_code} {r.text[:300]}")
    payload = r.json()
    return {
        "ref": ref,
        "elapsed_ms": elapsed_ms,
        "artifact_id": payload.get("artifact_id"),
        "chunks_written": payload.get("chunks_written"),
    }


async def main_async() -> None:
    ap = parse_profile_arg()
    ap.add_argument("--base-url", default=os.environ.get("SEDIMENT_BASE_URL", "https://hypeproof-sediment.fly.dev"))
    ap.add_argument("--limit", type=int, default=0, help="strict smoke limit; 0 means all manifest refs")
    ap.add_argument("--start-index", type=int, default=1, help="1-based manifest index to start from")
    args = ap.parse_args()

    token = (os.environ.get("SEDIMENT_TOKEN") or "").strip()
    if not token:
        raise BoaError("SEDIMENT_TOKEN is required")
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("profile") != args.profile:
        raise BoaError(f"manifest profile {manifest.get('profile')} != {args.profile}")

    items = [item for item in manifest.get("files", []) if item.get("vault_path")]
    if args.start_index < 1:
        raise BoaError("--start-index must be >= 1")
    items = items[args.start_index - 1 :]
    if args.limit:
        items = items[: args.limit]
    if not items:
        raise BoaError("manifest has no vault_path refs")

    async with httpx.AsyncClient() as client:
        identity = await _whoami(client, args.base_url.rstrip("/"), token)
        results = []
        for offset, item in enumerate(items, start=0):
            idx = args.start_index + offset
            ref = item["vault_path"]
            path = Path.cwd() / ref
            if not path.exists():
                raise BoaError(f"vault markdown missing: {ref}")
            result = await _ingest_one(client, args.base_url.rstrip("/"), token, ref, path.read_text(encoding="utf-8"))
            result["index"] = idx
            results.append(result)
            if idx % 25 == 0 or idx == len(items):
                print(json.dumps({"progress": idx, "total": len(items), "last_ref": ref}, ensure_ascii=False), flush=True)

    elapsed_total_ms = sum(int(r["elapsed_ms"]) for r in results)
    report = {
        "ok": True,
        "profile": args.profile,
        "base_url": args.base_url,
        "identity": {
            "member_id": identity.get("member_id"),
            "tenant_id": identity.get("tenant_id"),
            "role": identity.get("role"),
            "email": identity.get("email"),
        },
        "ingested": len(results),
        "elapsed_total_ms": elapsed_total_ms,
        "avg_ingest_ms": int(elapsed_total_ms / len(results)),
        "chunks_written": sum(int(r.get("chunks_written") or 0) for r in results),
        "first_ref": results[0]["ref"],
        "last_ref": results[-1]["ref"],
    }
    write_report("prod_ingest", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


def main() -> None:
    try:
        asyncio.run(main_async())
    except Exception as e:
        report = {"ok": False, "error": str(e), "error_type": e.__class__.__name__}
        write_report("prod_ingest", report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
