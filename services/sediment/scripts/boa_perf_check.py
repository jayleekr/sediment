"""Strict-local BOA corpus performance checks."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab_lib.chunker import chunk_markdown  # noqa: E402
from scripts.boa_lib import (  # noqa: E402
    BOA_MANIFEST_PATH,
    BoaError,
    load_taxonomy,
    parse_profile_arg,
    write_report,
)
from scripts.boa_local_retrieval_check import score  # noqa: E402


def main() -> None:
    ap = parse_profile_arg()
    ap.add_argument("--mode", default="strict-local", choices=["strict-local", "strict-online"])
    args = ap.parse_args()
    taxonomy = load_taxonomy()
    if args.mode != "strict-local":
        raise BoaError("boa_perf_check currently supports strict-local only")
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    docs = []
    chunk_count = 0
    t0 = time.perf_counter()
    for item in manifest.get("files", []):
        vault = item.get("vault_path")
        if not vault:
            raise BoaError("manifest missing vault_path; run converter first")
        path = Path.cwd() / vault
        text = path.read_text(encoding="utf-8")
        docs.append({"ref": vault, "text": text})
        chunk_count += len(chunk_markdown(text))
    chunk_elapsed_ms = int((time.perf_counter() - t0) * 1000)

    latencies: list[float] = []
    for scenario in taxonomy["golden_scenarios"]:
        q = scenario["anchor_query"]
        q0 = time.perf_counter()
        sorted(((score(q, d["text"]), d["ref"]) for d in docs), reverse=True)[:5]
        latencies.append((time.perf_counter() - q0) * 1000)
    latencies_sorted = sorted(latencies)
    p95 = latencies_sorted[int(len(latencies_sorted) * 0.95) - 1]
    thresholds = taxonomy["validation_thresholds"]
    min_chunks = thresholds["lecture_chunk_count_min"] if args.profile == "lecture_360" else 1
    max_chunks = thresholds["lecture_chunk_count_max"] if args.profile == "lecture_360" else 10000
    errors = []
    if not (min_chunks <= chunk_count <= max_chunks):
        errors.append({"code": "chunk_count_out_of_range", "actual": chunk_count, "min": min_chunks, "max": max_chunks})
    if p95 > thresholds["search_p95_ms_local_max"]:
        errors.append({"code": "local_search_p95_high", "actual_ms": p95, "max_ms": thresholds["search_p95_ms_local_max"]})
    report = {
        "ok": not errors,
        "profile": args.profile,
        "mode": args.mode,
        "doc_count": len(docs),
        "chunk_count": chunk_count,
        "chunk_estimate_ms": chunk_elapsed_ms,
        "local_retrieval_p95_ms": round(p95, 3),
        "errors": errors,
    }
    write_report("perf", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

