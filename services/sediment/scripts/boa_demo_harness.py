"""Top-level BOA strict harness."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.boa_lib import (  # noqa: E402
    BOA_NAS_ROOT,
    BOA_REPORT_ROOT,
    BOA_VAULT_ROOT,
    BoaError,
    harness_python,
    load_taxonomy,
    parse_profile_arg,
    write_report,
)


REQUIRED_MODULES = ["yaml", "openpyxl", "docx", "reportlab", "PIL", "tiktoken"]


def preflight() -> dict:
    taxonomy = load_taxonomy()
    missing = []
    for mod in REQUIRED_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # pragma: no cover - reports environment issue
            missing.append({"module": mod, "error": str(e)})
    area_total = sum(int(a["files"]) for a in taxonomy["areas"])
    type_total = sum(int(v) for v in taxonomy["file_type_mix"].values())
    errors = []
    if area_total != int(taxonomy["target_profile"]["total_files"]):
        errors.append({"code": "area_total_mismatch", "actual": area_total})
    if type_total != int(taxonomy["target_profile"]["total_files"]):
        errors.append({"code": "type_total_mismatch", "actual": type_total})
    if missing:
        errors.append({"code": "missing_required_modules", "modules": missing})
    for root in (BOA_NAS_ROOT, BOA_VAULT_ROOT, BOA_REPORT_ROOT):
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    report = {"ok": not errors, "errors": errors, "area_total": area_total, "type_total": type_total}
    write_report("preflight", report)
    return report


def run_step(args: list[str], timeout: int) -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [harness_python(), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return {
        "cmd": [harness_python(), *args],
        "returncode": proc.returncode,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def main() -> None:
    ap = parse_profile_arg()
    ap.add_argument("--mode", default="strict-local", choices=["strict-local", "strict-online"])
    ap.add_argument("--preflight", action="store_true")
    args = ap.parse_args()
    taxonomy = load_taxonomy()
    timeout = int(taxonomy["execution_policy"]["subprocess_timeout_seconds"])
    pf = preflight()
    if args.preflight:
        print(json.dumps(pf, ensure_ascii=False, sort_keys=True))
        if not pf["ok"]:
            raise SystemExit(1)
        return
    if not pf["ok"]:
        print(json.dumps(pf, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)
    if args.mode == "strict-online":
        result = run_step(
            ["services/sediment/scripts/boa_e2e.py", "--profile", args.profile, "--mode", "strict-online"],
            max(timeout, 900),
        )
        report = {"ok": result["returncode"] == 0, "profile": args.profile, "mode": args.mode, "preflight": pf, "steps": [result]}
        write_report("harness", report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        if result["returncode"] != 0:
            raise SystemExit(1)
        return
    steps = [
        ["services/sediment/scripts/generate_boa_synthetic_files.py", "--profile", args.profile],
        ["services/sediment/scripts/validate_boa_synthetic_files.py", "--profile", args.profile],
        ["services/sediment/scripts/convert_boa_files_to_vault.py", "--profile", args.profile],
        ["services/sediment/scripts/boa_local_retrieval_check.py", "--profile", args.profile],
        ["services/sediment/scripts/boa_perf_check.py", "--profile", args.profile, "--mode", "strict-local"],
        ["services/sediment/scripts/boa_e2e.py", "--profile", args.profile, "--mode", "strict-local"],
    ]
    results = []
    ok = True
    for step in steps:
        result = run_step(step, timeout)
        results.append(result)
        if result["returncode"] != 0:
            ok = False
            break
    report = {"ok": ok, "profile": args.profile, "mode": args.mode, "preflight": pf, "steps": results}
    write_report("harness", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
