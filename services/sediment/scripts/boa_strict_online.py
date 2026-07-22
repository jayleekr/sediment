"""Strict-online BOA E2E against live local Sediment services.

No fallback behavior:
- platform, ingester, langgraph must all be healthy;
- dev token must mint successfully;
- all BOA markdown artifacts must ingest;
- embeddings for BOA chunks must be non-zero;
- CLI search/read/ask must pass for every scenario.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab_lib.db import service_session  # noqa: E402
from scripts.boa_lib import (  # noqa: E402
    BOA_MANIFEST_PATH,
    BoaError,
    load_taxonomy,
    parse_profile_arg,
    repo_rel,
    write_report,
)

DEFAULT_EMAIL = "jay.lee@sonatus.com"


def _run(cmd: list[str], env: dict[str, str], timeout: int) -> dict[str, Any]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    parsed = None
    if proc.stdout.strip().startswith("{"):
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "elapsed_ms": elapsed_ms,
        "stdout_tail": proc.stdout[-3000:],
        "stderr_tail": proc.stderr[-3000:],
        "json": parsed,
    }


def _cli_cmd() -> list[str]:
    binary = Path("services/sediment-cli/target/debug/sediment")
    if binary.exists():
        return [str(binary)]
    return ["cargo", "run", "--quiet", "--manifest-path", "services/sediment-cli/Cargo.toml", "--"]


def _strict_secret_check() -> dict[str, Any]:
    embedding_ok = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    llm_provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    llm_ok = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or llm_provider == "claude_cli"
    )
    errors = []
    if not embedding_ok:
        errors.append("missing GEMINI_API_KEY or OPENAI_API_KEY for non-zero embeddings")
    if not llm_ok:
        errors.append("missing ANTHROPIC_API_KEY/GEMINI_API_KEY or LLM_PROVIDER=claude_cli for ask")
    if llm_provider == "offline":
        errors.append("LLM_PROVIDER=offline is forbidden for strict-online")
    if (os.environ.get("EMBEDDING_PROVIDER") or "").strip().lower() == "zero":
        errors.append("EMBEDDING_PROVIDER=zero is forbidden for strict-online")
    if errors:
        raise BoaError("; ".join(errors))
    return {
        "embedding_key": "present",
        "llm_key_or_provider": "present",
        "llm_provider": llm_provider or "auto",
        "embedding_provider": (os.environ.get("EMBEDDING_PROVIDER") or "auto").strip().lower(),
    }


async def _health(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    r = await client.get(url, timeout=5)
    return {"url": url, "status_code": r.status_code, "json": r.json() if r.headers.get("content-type", "").startswith("application/json") else None}


async def _mint_token(client: httpx.AsyncClient, base_url: str, email: str) -> dict[str, Any]:
    r = await client.post(f"{base_url}/api/v1/auth/dev-token", json={"email": email}, timeout=10)
    if r.status_code != 200:
        raise BoaError(f"dev-token failed {r.status_code}: {r.text[:300]}")
    return r.json()


async def _ingest_all(client: httpx.AsyncClient, base_url: str, token: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    for item in manifest.get("files", []):
        vault = item.get("vault_path")
        if not vault:
            raise BoaError("manifest item missing vault_path; run strict-local conversion first")
        body = (Path.cwd() / vault).read_text(encoding="utf-8")
        payload = {"ref": vault, "type": "note", "body": body}
        r = await client.post(f"{base_url}/api/v1/ingest/document", json=payload, headers=headers, timeout=120)
        result = {"ref": vault, "status_code": r.status_code}
        if r.status_code == 200:
            result.update(r.json())
        else:
            result["error"] = r.text[:300]
        results.append(result)
        if r.status_code != 200:
            raise BoaError(f"ingest failed for {vault}: {r.status_code} {r.text[:300]}")
    return results


async def _verify_nonzero_embeddings(tenant_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    refs = [item["vault_path"] for item in manifest.get("files", []) if item.get("vault_path")]
    if not refs:
        raise BoaError("no refs for embedding verification")
    zero = "[" + ",".join("0.0" for _ in range(1536)) + "]"
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT count(*) AS chunks,
                   count(*) FILTER (WHERE c.embedding = CAST(:zero AS vector)) AS zero_chunks
            FROM chunks c
            JOIN artifacts a ON a.id = c.artifact_id
            WHERE a.tenant_id = :tid AND a.ref = ANY(:refs)
        """), {"tid": tenant_id, "refs": refs, "zero": zero})
        row = r.first()
    chunks = int(row[0] or 0)
    zero_chunks = int(row[1] or 0)
    if chunks <= 0:
        raise BoaError("embedding verification found zero chunks")
    if zero_chunks > 0:
        raise BoaError(f"zero-vector embeddings are not allowed: {zero_chunks}/{chunks}")
    return {"chunks": chunks, "zero_chunks": zero_chunks}


def _require_cli_success(result: dict[str, Any], label: str) -> dict[str, Any]:
    if result["returncode"] != 0:
        raise BoaError(f"{label} failed: {result['stdout_tail'] or result['stderr_tail']}")
    if not result.get("json"):
        raise BoaError(f"{label} did not return JSON")
    return result["json"]


async def main_async() -> None:
    ap = parse_profile_arg()
    ap.add_argument("--base-url", default=os.environ.get("SEDIMENT_BASE_URL", "http://localhost:10100"))
    ap.add_argument("--langgraph-url", default=os.environ.get("SEDIMENT_LANGGRAPH_URL", "http://localhost:10020"))
    ap.add_argument("--ingester-url", default=os.environ.get("SEDIMENT_INGESTER_URL", "http://localhost:11000"))
    ap.add_argument("--email", default=os.environ.get("BOA_SEDIMENT_EMAIL", DEFAULT_EMAIL))
    ap.add_argument("--skip-ingest", action="store_true")
    args = ap.parse_args()

    taxonomy = load_taxonomy()
    http_timeout = int(taxonomy["execution_policy"]["http_timeout_seconds"])
    secret_check = _strict_secret_check()
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("profile") != args.profile:
        raise BoaError(f"manifest profile {manifest.get('profile')} != {args.profile}")

    async with httpx.AsyncClient(timeout=http_timeout) as client:
        health = [
            await _health(client, f"{args.base_url}/healthz"),
            await _health(client, f"{args.langgraph_url}/healthz"),
            await _health(client, f"{args.ingester_url}/healthz"),
        ]
        bad = [h for h in health if h["status_code"] != 200]
        if bad:
            raise BoaError(f"health checks failed: {bad}")
        token_payload = await _mint_token(client, args.base_url, args.email)
        token = token_payload["token"]
        ingest_results = [] if args.skip_ingest else await _ingest_all(client, args.base_url, token, manifest)
        embedding_check = await _verify_nonzero_embeddings(token_payload["tenant_id"], manifest)

    cli = _cli_cmd()
    env = os.environ.copy()
    env.update({
        "SEDIMENT_TOKEN": token,
        "SEDIMENT_ACCOUNT": args.email,
        "SEDIMENT_BASE_URL": args.base_url,
        "SEDIMENT_DEV_MODE": "1",
    })
    cli_results = []
    # whoami
    whoami = _run([*cli, "--format", "json", "whoami"], env, timeout=60)
    _require_cli_success(whoami, "cli whoami")
    cli_results.append({"label": "whoami", **whoami})

    manifest_by_ref = {item["vault_path"]: item for item in manifest.get("files", []) if item.get("vault_path")}
    del manifest_by_ref
    for scenario in taxonomy["golden_scenarios"]:
        q = scenario["anchor_query"]
        search = _run([*cli, "--format", "json", "search", q, "--limit", "5"], env, timeout=90)
        search_json = _require_cli_success(search, f"cli search {scenario['id']}")
        items = search_json.get("items") or []
        if not items:
            raise BoaError(f"cli search returned no items for {scenario['id']}")
        top_ref = items[0].get("ref")
        if not top_ref:
            raise BoaError(f"cli search top item missing ref for {scenario['id']}")
        read = _run([*cli, "--format", "json", "read", top_ref], env, timeout=60)
        read_json = _require_cli_success(read, f"cli read {scenario['id']}")
        body = json.dumps(read_json, ensure_ascii=False)
        missing_terms = [term for term in scenario["expected_terms"] if term not in body]
        if missing_terms:
            raise BoaError(f"cli read missing expected terms for {scenario['id']}: {missing_terms}")
        ask = _run([*cli, "--format", "json", "ask", q], env, timeout=180)
        ask_json = _require_cli_success(ask, f"cli ask {scenario['id']}")
        answer = ask_json.get("answer") or ""
        citations = ask_json.get("citations") or []
        if ask_json.get("warning"):
            raise BoaError(f"cli ask returned warning for {scenario['id']}: {ask_json['warning']}")
        if len(citations) < 1:
            raise BoaError(f"cli ask returned no citations for {scenario['id']}")
        if not answer:
            raise BoaError(f"cli ask returned empty answer for {scenario['id']}")
        cli_results.append({
            "label": scenario["id"],
            "search_ms": search["elapsed_ms"],
            "read_ms": read["elapsed_ms"],
            "ask_ms": ask["elapsed_ms"],
            "top_ref": top_ref,
            "citation_count": len(citations),
        })

    report = {
        "ok": True,
        "profile": args.profile,
        "base_url": args.base_url,
        "secret_check": secret_check,
        "health": health,
        "ingested": len(ingest_results),
        "embedding_check": embedding_check,
        "cli_results": cli_results,
    }
    write_report("strict_online", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


def main() -> None:
    import asyncio

    try:
        asyncio.run(main_async())
    except Exception as e:
        report = {"ok": False, "error": str(e), "error_type": e.__class__.__name__}
        write_report("strict_online", report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
