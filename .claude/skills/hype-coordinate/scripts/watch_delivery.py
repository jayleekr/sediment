#!/usr/bin/env python3
"""Run token-free delivery scans and wake Claude A only for pending work.

Run the loop as a live child of the Claude A cmux session, for example as one
background shell task in that session. cmux's cmuxOnly socket rejects every
process it did not start: a launchd agent, and also a daemon whose parent exited
so that launchd adopted it (#180). Do not detach it with nohup, setsid or `&`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DELTA = HERE / "delivery_delta.py"
WAKE = HERE / "wake_role.py"
SOCKET_DENIED = "socket_access_denied"
EXIT_SOCKET_DENIED = 3


def run_json(command: list[str]) -> tuple[int, dict[str, Any], str]:
    env = os.environ.copy()
    env["HYPEPROOF_HARNESS"] = str(ROOT)
    proc = subprocess.run(
        command, text=True, capture_output=True, check=False, env=env
    )
    output = proc.stdout.strip()
    try:
        result = json.loads(output) if output else {}
    except json.JSONDecodeError:
        result = {}
    error = proc.stderr.strip() or ("invalid JSON output" if not result else "")
    return proc.returncode, result, error


def record_url(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    current = payload.get("after") or payload
    if not isinstance(current, dict):
        current = payload.get("before") or {}
    return str(current.get("url") or "")


def scan_command(state_file: Path, batch_size: int) -> list[str]:
    return [
        sys.executable,
        str(DELTA),
        "--state-file",
        str(state_file),
        "--batch-size",
        str(batch_size),
    ]


def wake_command(state_file: Path, record: dict[str, Any]) -> list[str]:
    return [
        sys.executable,
        str(WAKE),
        "--role",
        "a",
        "--packet",
        str(record["token"]),
        "--work-url",
        record_url(record),
        "--state-file",
        str(state_file),
        "--apply",
    ]


def scan_once(state_file: Path, batch_size: int, apply: bool) -> tuple[int, dict[str, Any]]:
    code, delta, error = run_json(scan_command(state_file, batch_size))
    if code != 0:
        return 2, {"status": "unknown", "error": error, "dispatched": False}

    backlog = delta.get("delta", {}).get("backlog", [])
    if not delta.get("changed") or not backlog:
        return 0, {"status": "unchanged", "dispatched": False}

    record = backlog[0]
    url = record_url(record)
    if not record.get("token") or not url:
        return 2, {"status": "unknown", "error": "pending record lacks token or URL", "dispatched": False}

    command = wake_command(state_file, record)
    if not apply:
        return 0, {
            "status": "would_dispatch",
            "token": record["token"],
            "url": url,
            "dispatched": False,
            "command": command[:-1],
        }

    wake_code, wake, wake_error = run_json(command)
    if wake_code != 0:
        return 2, {
            "status": "blocked",
            "token": record["token"],
            "url": url,
            "error": wake_error,
            "dispatched": False,
        }
    return 0, {
        "status": wake.get("status", "submitted_pending_ack"),
        "token": record["token"],
        "url": url,
        "dispatch_attempt": wake.get("dispatch_attempt"),
        "accepted": False,
        "dispatched": True,
    }


def tick(state_file: Path, batch_size: int, apply: bool) -> tuple[int, dict[str, Any]]:
    lock_file = state_file.with_suffix(state_file.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0, {"status": "busy", "dispatched": False}
        return scan_once(state_file, batch_size, apply)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


def run_loop(args: argparse.Namespace, state_file: Path) -> int:
    if not 60 <= args.interval <= 3600:
        emit({"status": "error", "error": "--interval must be between 60 and 3600 seconds"})
        return 2
    if os.getppid() == 1:
        emit({
            "status": "blocked",
            "error": f"{SOCKET_DENIED}: watcher was adopted by launchd; start it as a "
            "live child of the Claude A cmux session",
        })
        return EXIT_SOCKET_DENIED

    loop_lock = state_file.with_suffix(state_file.suffix + ".loop.lock")
    loop_lock.parent.mkdir(parents=True, exist_ok=True)
    with loop_lock.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit({"status": "already_running", "lock": str(loop_lock)})
            return 0
        lock.seek(0)
        lock.truncate()
        lock.write(f"{os.getpid()}\n")
        lock.flush()

        stopping = []
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stopping.append(True))

        ticks = 0
        while not stopping:
            code, payload = tick(state_file, args.batch_size, args.apply)
            payload["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            emit(payload)
            if SOCKET_DENIED in str(payload.get("error", "")):
                # Retrying cannot recover a rejected socket; exit so A sees the task end.
                return EXIT_SOCKET_DENIED
            ticks += 1
            if args.max_ticks and ticks >= args.max_ticks:
                return 0
            for _ in range(args.interval):
                if stopping:
                    break
                time.sleep(1)
        emit({"status": "stopped", "ticks": ticks})
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Scan every --interval seconds until stopped")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--max-ticks", type=int, default=0, help="Stop the loop after N ticks (0 = unbounded)")
    args = parser.parse_args()

    state_file = args.state_file.expanduser().resolve()
    if args.loop:
        return run_loop(args, state_file)
    code, payload = tick(state_file, args.batch_size, args.apply)
    emit(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
