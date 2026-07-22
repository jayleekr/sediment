"""Strict local retrieval checks over generated BOA markdown artifacts."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.boa_lib import BOA_MANIFEST_PATH, BoaError, load_taxonomy, parse_profile_arg, write_report  # noqa: E402


def tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9가-힣]+", text) if len(t) >= 2]


def score(query: str, text: str) -> float:
    q = Counter(tokens(query))
    d = Counter(tokens(text))
    if not q:
        return 0.0
    return sum(min(q[t], d.get(t, 0)) for t in q) / max(1, sum(q.values()))


def main() -> None:
    ap = parse_profile_arg()
    args = ap.parse_args()
    taxonomy = load_taxonomy()
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    docs = []
    for item in manifest.get("files", []):
        vault = item.get("vault_path")
        if not vault:
            continue
        path = Path.cwd() / vault
        docs.append({"ref": vault, "text": path.read_text(encoding="utf-8")})
    if not docs:
        raise BoaError("no vault markdown docs found; run convert_boa_files_to_vault.py first")
    failures = []
    results = []
    for scenario in taxonomy["golden_scenarios"]:
        q = scenario["anchor_query"]
        ranked = sorted(((score(q, d["text"]), d["ref"]) for d in docs), reverse=True)
        top_refs = []
        for s, ref in ranked:
            if s <= 0:
                continue
            if ref not in top_refs:
                top_refs.append(ref)
            if len(top_refs) >= 5:
                break
        hit_text = "\n".join((Path.cwd() / ref).read_text(encoding="utf-8") for ref in top_refs[:3])
        missing_terms = [term for term in scenario["expected_terms"] if term not in hit_text]
        ok = len(top_refs) >= 3 and not missing_terms
        row = {"scenario": scenario["id"], "query": q, "ok": ok, "top_refs": top_refs[:5], "missing_terms": missing_terms}
        results.append(row)
        if not ok:
            failures.append(row)
    report = {"ok": not failures, "profile": args.profile, "doc_count": len(docs), "results": results, "failure_count": len(failures)}
    write_report("local_retrieval", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
