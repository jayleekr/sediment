"""Explicit credential-free RPC to a Work host with connected GitHub tools.

The host is a trusted local peer, like gh. This is not an approval boundary.
Use a fresh private directory per command; requests are never retried on timeout.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from functools import lru_cache


def enabled():
    return bool(os.environ.get("HYPE_PR_WORK_DIR"))


@lru_cache(maxsize=1)
def immutable_cache(root):
    path = root / "immutable-cache.json"
    return json.loads(path.read_text()) if path.exists() else {}


def exchange(operation, payload, timeout=60):
    root = Path(os.environ["HYPE_PR_WORK_DIR"])
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("Work transport needs an existing absolute private directory")
    if root.stat().st_mode & 0o077:
        raise ValueError("Work transport directory must have mode 0700")
    path = payload.get("path", "")
    if operation == "read" and re.fullmatch(r"repos/[^/]+/[^/]+/contents/.+\?ref=[a-f0-9]{40}", path):
        cached = immutable_cache(root).get(path)
        if cached is not None:
            return cached
    request_id = uuid.uuid4().hex
    request = {"version": 1, "id": request_id, "operation": operation, "payload": payload}
    temporary = root / f"{request_id}.tmp"
    temporary.write_text(json.dumps(request))
    temporary.chmod(0o600)
    temporary.replace(root / f"{request_id}.request")
    response = root / f"{request_id}.response"
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if response.exists():
                result = json.loads(response.read_text())
                if result.get("id") != request_id or result.get("version") != 1:
                    raise ValueError("Work transport response mismatch")
                if result.get("ok") is not True:
                    raise ValueError("Work GitHub operation failed; inspect host diagnostics")
                return result["result"]
            time.sleep(0.05)
        raise ValueError("Work transport timed out; reconcile remote state before retrying mutations")
    finally:
        (root / f"{request_id}.request").unlink(missing_ok=True)
        response.unlink(missing_ok=True)


def poll(directory):
    """Atomically deliver host replies, then return pending requests without source data."""
    root = Path(directory)
    for reply in root.glob("*.reply"):
        if re.fullmatch(r"[a-f0-9]{32}", reply.stem):
            reply.replace(reply.with_suffix(".response"))
    return {"done": (root / "done").read_text() if (root / "done").exists() else None,
            "requests": [json.loads(p.read_text()) for p in sorted(root.glob("*.request"))
                         if not p.with_suffix(".response").exists()]}


if __name__ == "__main__":
    print(json.dumps(poll(sys.argv[1])))
