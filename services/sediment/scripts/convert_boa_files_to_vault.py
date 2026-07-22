"""Convert BOA synthetic NAS files into Sediment markdown artifacts."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.boa_lib import (  # noqa: E402
    BOA_MANIFEST_PATH,
    BOA_VAULT_ROOT,
    BoaError,
    load_taxonomy,
    parse_profile_arg,
    repo_rel,
    write_json,
    write_report,
)


def slugify(text: str) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣]+", "-", text).strip("-").lower()
    return text[:90] or "boa-doc"


def main() -> None:
    ap = parse_profile_arg()
    args = ap.parse_args()
    taxonomy = load_taxonomy()
    del taxonomy
    if not BOA_MANIFEST_PATH.exists():
        raise BoaError(f"manifest missing: {BOA_MANIFEST_PATH}")
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    converted = []
    for item in manifest.get("files", []):
        meta_path = Path.cwd() / item["metadata_path"]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        area = meta["folder_area"]
        slug = slugify(meta["title"])
        vault_path = BOA_VAULT_ROOT / area / f"{slug}.md"
        source_ref = item["path"]
        expected_queries = meta.get("expected_queries") or []
        body = meta["body_text"]
        fm = [
            "---",
            f"date: {meta['year']}-01-01",
            f"slug: {slug}",
            "lang: ko",
            "status: published",
            "synthetic: true",
            f"boa_area: {area}",
            f"doc_type: {meta['doc_type']}",
            f"lecture_module: {meta['lecture_module']}",
            f"source_file: {source_ref}",
            "---",
            "",
        ]
        summary = [
            f"# {meta['title']}",
            "",
            f"원본 파일: `{source_ref}`",
            f"문서 유형: `{meta['doc_type']}`",
            f"강의 모듈: `{meta['lecture_module']}`",
            "실제 개인정보 포함 여부: false",
            "",
            "## 검색 키워드",
            " ".join([meta["title"], meta["doc_type"], meta["folder_area"], meta["lecture_module"], *expected_queries]),
            "",
            "## 원문 요약",
            "주요 체크리스트, 담당자 확인, 합성 케이스 ID, 강의 질문 연결을 포함한다.",
            "",
            "## 체크리스트 상세",
        ]
        vault_path.parent.mkdir(parents=True, exist_ok=True)
        vault_path.write_text("\n".join(fm + summary) + "\n\n" + body + "\n", encoding="utf-8")
        item["vault_path"] = repo_rel(vault_path)
        converted.append(item["vault_path"])
    write_json(BOA_MANIFEST_PATH, manifest)
    report = {"ok": True, "profile": args.profile, "converted": len(converted), "vault_root": repo_rel(BOA_VAULT_ROOT)}
    write_report("convert_vault", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
