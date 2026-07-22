"""Delete BOA generated artifacts by manifest refs.

Strict safety:
- deletes only refs listed in `assets/generated/boa_manifest.json`;
- requires a tenant slug;
- dry-run by default;
- chunks are removed by artifacts ON DELETE CASCADE.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import bindparam, text  # noqa: E402

from lab_lib.db import service_session  # noqa: E402
from scripts.boa_lib import BOA_MANIFEST_PATH, BoaError, repo_rel, write_report  # noqa: E402


async def _tenant_id(slug: str) -> str:
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text FROM tenants WHERE slug = :slug"), {"slug": slug})
        row = r.first()
    if not row:
        raise BoaError(f"tenant not found: {slug}")
    return str(row[0])


async def _count_refs(tenant_id: str, refs: list[str]) -> dict:
    q = text("""
        SELECT count(*) AS artifacts,
               COALESCE((SELECT count(*) FROM chunks c
                         JOIN artifacts a2 ON a2.id = c.artifact_id
                         WHERE a2.tenant_id = CAST(:tid AS uuid)
                           AND a2.ref IN :refs), 0) AS chunks
        FROM artifacts a
        WHERE a.tenant_id = CAST(:tid AS uuid)
          AND a.ref IN :refs
    """).bindparams(bindparam("refs", expanding=True))
    async with service_session() as s:
        r = await s.execute(q, {"tid": tenant_id, "refs": refs})
        row = r.first()
    return {"artifacts": int(row[0] or 0), "chunks": int(row[1] or 0)}


async def _delete_refs(tenant_id: str, refs: list[str]) -> int:
    q = text("""
        DELETE FROM artifacts
        WHERE tenant_id = CAST(:tid AS uuid)
          AND ref IN :refs
    """).bindparams(bindparam("refs", expanding=True))
    async with service_session() as s:
        r = await s.execute(q, {"tid": tenant_id, "refs": refs})
        await s.execute(text("""
            INSERT INTO audit_log (tenant_id, action, payload)
            VALUES (CAST(:tid AS uuid), 'boa_demo.cleanup',
                    jsonb_build_object('ref_count', CAST(:n AS int)))
        """), {"tid": tenant_id, "n": len(refs)})
        return int(r.rowcount or 0)


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="tenant slug")
    ap.add_argument("--apply", action="store_true", help="actually delete; default is dry-run")
    args = ap.parse_args()
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    refs = sorted({item["vault_path"] for item in manifest.get("files", []) if item.get("vault_path")})
    if not refs:
        raise BoaError("manifest contains no vault_path refs")
    tid = await _tenant_id(args.tenant)
    before = await _count_refs(tid, refs)
    deleted = 0
    after = before
    if args.apply:
        deleted = await _delete_refs(tid, refs)
        after = await _count_refs(tid, refs)
    report = {
        "ok": True,
        "tenant": args.tenant,
        "manifest": repo_rel(BOA_MANIFEST_PATH),
        "ref_count": len(refs),
        "dry_run": not args.apply,
        "before": before,
        "deleted": deleted,
        "after": after,
    }
    write_report("cleanup", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain()))
    except Exception as e:
        report = {"ok": False, "error": str(e), "error_type": e.__class__.__name__}
        write_report("cleanup", report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

