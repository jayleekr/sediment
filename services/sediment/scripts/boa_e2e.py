"""BOA strict E2E orchestration."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.boa_lib import BoaError, harness_python, load_taxonomy, parse_profile_arg, write_report  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent


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
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "cmd": [harness_python(), *args],
        "returncode": proc.returncode,
        "elapsed_ms": elapsed_ms,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def main() -> None:
    ap = parse_profile_arg()
    ap.add_argument("--mode", default="strict-local", choices=["strict-local", "strict-online"])
    args = ap.parse_args()
    taxonomy = load_taxonomy()
    timeout = int(taxonomy["execution_policy"]["subprocess_timeout_seconds"])
    if args.mode == "strict-online":
        result = run_step(
            ["services/sediment/scripts/boa_strict_online.py", "--profile", args.profile],
            max(timeout, 900),
        )
        report = {"ok": result["returncode"] == 0, "profile": args.profile, "mode": args.mode, "steps": [result]}
        write_report("e2e", report)
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
    ]
    results = []
    ok = True
    for step in steps:
        result = run_step(step, timeout)
        results.append(result)
        if result["returncode"] != 0:
            ok = False
            break
    report = {"ok": ok, "profile": args.profile, "mode": args.mode, "steps": results}
    write_report("e2e", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
