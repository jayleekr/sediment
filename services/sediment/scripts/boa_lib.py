"""Shared helpers for the BOA synthetic corpus strict harness."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SVC_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = SVC_ROOT / "data" / "boa_file_taxonomy.yaml"

BOA_NAS_ROOT = REPO_ROOT / "assets" / "generated" / "boa_nas"
BOA_VAULT_ROOT = REPO_ROOT / "assets" / "generated" / "boa_vault_md"
BOA_REPORT_ROOT = REPO_ROOT / "assets" / "generated" / "boa_reports"
BOA_MANIFEST_PATH = REPO_ROOT / "assets" / "generated" / "boa_manifest.json"

FORBIDDEN_SUBSTITUTE_SUFFIXES = (".docx.txt", ".pdf.txt", ".png.txt", ".jpg.txt")
FORBIDDEN_PII_PATTERNS = {
    "resident_registration_like": re.compile(r"\b\d{6}-[1-4]\d{6}\b"),
    "phone_like": re.compile(r"\b01[016789]-\d{3,4}-\d{4}\b"),
}


def harness_python() -> str:
    """Return the repo venv Python when present; otherwise current Python."""
    venv = SVC_ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable


class BoaError(RuntimeError):
    """Strict harness failure."""


@dataclass(frozen=True)
class HarnessResult:
    ok: bool
    report_path: Path
    data: dict[str, Any]


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_taxonomy() -> dict[str, Any]:
    data = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BoaError(f"invalid taxonomy: {TAXONOMY_PATH}")
    policy = data.get("execution_policy") or {}
    if policy.get("allow_fallback") is not False:
        raise BoaError("taxonomy must set execution_policy.allow_fallback=false")
    if policy.get("substitute_outputs_allowed") is not False:
        raise BoaError("taxonomy must set execution_policy.substitute_outputs_allowed=false")
    return data


def profile_total(taxonomy: dict[str, Any], profile: str) -> int:
    profiles = taxonomy.get("profiles") or {}
    if profile not in profiles:
        raise BoaError(f"unknown profile {profile!r}")
    return int(profiles[profile]["total_files"])


def ensure_safe_generated_path(path: Path) -> None:
    resolved = path.resolve()
    allowed = [
        BOA_NAS_ROOT.resolve(),
        BOA_VAULT_ROOT.resolve(),
        BOA_REPORT_ROOT.resolve(),
        BOA_MANIFEST_PATH.resolve(),
    ]
    if resolved == BOA_MANIFEST_PATH.resolve():
        return
    if not any(resolved == root or root in resolved.parents for root in allowed[:3]):
        raise BoaError(f"unsafe generated path outside BOA roots: {path}")


def mkdirs() -> None:
    for p in (BOA_NAS_ROOT, BOA_VAULT_ROOT, BOA_REPORT_ROOT):
        p.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    ensure_safe_generated_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_report(name: str, data: dict[str, Any]) -> Path:
    mkdirs()
    data = {**data, "report": name, "generated_at_epoch": int(time.time())}
    latest = BOA_REPORT_ROOT / f"latest_{name}.json"
    write_json(latest, data)
    return latest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_from_manifest() -> None:
    if not BOA_MANIFEST_PATH.exists():
        return
    manifest = json.loads(BOA_MANIFEST_PATH.read_text(encoding="utf-8"))
    for item in manifest.get("files", []):
        for key in ("path", "metadata_path", "vault_path"):
            value = item.get(key)
            if not value:
                continue
            path = REPO_ROOT / value
            ensure_safe_generated_path(path)
            if path.exists():
                path.unlink()
    for root in (BOA_NAS_ROOT, BOA_VAULT_ROOT):
        if root.exists():
            for child in sorted(root.rglob("*"), reverse=True):
                if child.is_dir():
                    try:
                        child.rmdir()
                    except OSError:
                        pass


def parse_profile_arg() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="lecture_360", choices=["sample_60", "lecture_360", "stress_1200"])
    return ap


def scaled_area_counts(taxonomy: dict[str, Any], profile: str) -> dict[str, int]:
    target = profile_total(taxonomy, profile)
    areas = taxonomy["areas"]
    base_total = sum(int(a["files"]) for a in areas)
    raw = [(a["id"], int(a["files"]) * target / base_total) for a in areas]
    counts = {aid: int(v) for aid, v in raw}
    remaining = target - sum(counts.values())
    by_fraction = sorted(raw, key=lambda x: x[1] - int(x[1]), reverse=True)
    for aid, _ in by_fraction[:remaining]:
        counts[aid] += 1
    return counts


def choose_docs_for_profile(taxonomy: dict[str, Any], profile: str) -> list[dict[str, Any]]:
    """Expand taxonomy doc types into concrete planned documents."""
    rng = random.Random(int(taxonomy["execution_policy"]["deterministic_seed"]) + profile_total(taxonomy, profile))
    area_counts = scaled_area_counts(taxonomy, profile)
    docs: list[dict[str, Any]] = []
    for area in taxonomy["areas"]:
        target = area_counts[area["id"]]
        base = int(area["files"])
        produced = 0
        doc_types = area["doc_types"]
        for dt in doc_types:
            n = round(int(dt["count"]) * target / base)
            if n == 0 and produced < target:
                n = 1
            for i in range(n):
                docs.append({"area": area, "doc_type": dt, "ordinal": i + 1})
                produced += 1
        while produced > target:
            docs.pop()
            produced -= 1
        while produced < target:
            dt = rng.choice(doc_types)
            docs.append({"area": area, "doc_type": dt, "ordinal": produced + 1})
            produced += 1
    rng.shuffle(docs)
    return docs[: profile_total(taxonomy, profile)]


def detect_pii_text(text: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in FORBIDDEN_PII_PATTERNS.items():
        if pattern.search(text):
            hits.append(name)
    return hits


def assert_no_forbidden_substitute(path: Path) -> None:
    s = path.name.lower()
    if any(s.endswith(suffix) for suffix in FORBIDDEN_SUBSTITUTE_SUFFIXES):
        raise BoaError(f"forbidden substitute output: {path}")


def reset_generated_roots() -> None:
    clean_from_manifest()
    for root in (BOA_NAS_ROOT, BOA_VAULT_ROOT, BOA_REPORT_ROOT):
        ensure_safe_generated_path(root)
        root.mkdir(parents=True, exist_ok=True)


def remove_generated_roots_for_fresh_run() -> None:
    """Remove only BOA generated roots. Used when no manifest exists."""
    for root in (BOA_NAS_ROOT, BOA_VAULT_ROOT):
        ensure_safe_generated_path(root)
        if root.exists():
            shutil.rmtree(root)
    mkdirs()
