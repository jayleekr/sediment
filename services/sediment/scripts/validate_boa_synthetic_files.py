"""Validate strict BOA synthetic files and metadata."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.boa_lib import (  # noqa: E402
    BOA_MANIFEST_PATH,
    BoaError,
    assert_no_forbidden_substitute,
    detect_pii_text,
    load_taxonomy,
    parse_profile_arg,
    profile_total,
    repo_rel,
    write_report,
)


REQUIRED_METADATA = {
    "tenant_slug",
    "source_pattern",
    "folder_area",
    "doc_type",
    "year",
    "owner_role",
    "lecture_module",
    "demo_relevance",
    "contains_real_phi",
    "synthetic_patient_ids",
    "expected_queries",
    "title",
    "body_text",
    "file_extension",
}


def main() -> None:
    ap = parse_profile_arg()
    args = ap.parse_args()
    taxonomy = load_taxonomy()
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    expected_total = profile_total(taxonomy, args.profile)
    errors: list[dict] = []
    files = manifest.get("files") or []
    if manifest.get("profile") != args.profile:
        errors.append({"code": "profile_mismatch", "manifest_profile": manifest.get("profile"), "expected": args.profile})
    if len(files) != expected_total:
        errors.append({"code": "file_count_mismatch", "actual": len(files), "expected": expected_total})

    ext_counts: Counter[str] = Counter()
    area_counts: Counter[str] = Counter()
    seen_paths: set[str] = set()
    for item in files:
        if item["path"] in seen_paths:
            errors.append({"code": "duplicate_manifest_path", "path": item["path"]})
        seen_paths.add(item["path"])
        path = Path.cwd() / item["path"]
        meta_path = Path.cwd() / item["metadata_path"]
        try:
            assert_no_forbidden_substitute(path)
        except BoaError as e:
            errors.append({"code": "forbidden_substitute", "path": item.get("path"), "error": str(e)})
        if not path.exists() or path.stat().st_size <= 0:
            errors.append({"code": "missing_or_empty_file", "path": item.get("path")})
            continue
        if not meta_path.exists():
            errors.append({"code": "missing_metadata", "path": item.get("metadata_path")})
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        missing = sorted(REQUIRED_METADATA - set(meta))
        if missing:
            errors.append({"code": "metadata_missing_fields", "path": item.get("metadata_path"), "fields": missing})
        if meta.get("contains_real_phi") is not False:
            errors.append({"code": "contains_real_phi_not_false", "path": item.get("metadata_path")})
        pii_hits = detect_pii_text(json.dumps(meta, ensure_ascii=False))
        if pii_hits:
            errors.append({"code": "pii_like_metadata", "path": item.get("metadata_path"), "hits": pii_hits})
        ext_counts[item.get("extension", "")] += 1
        area_counts[item.get("area", "")] += 1

    report = {
        "ok": not errors,
        "profile": args.profile,
        "files": len(files),
        "extension_counts": dict(sorted(ext_counts.items())),
        "area_counts": dict(sorted(area_counts.items())),
        "errors": errors[:100],
        "error_count": len(errors),
        "manifest": repo_rel(BOA_MANIFEST_PATH),
    }
    write_report("validate_files", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
