#!/usr/bin/env python3
"""Run every golden-set case against the LIVE prod backend + report.

Reads ~/Library/Application Support/sediment/credentials.json for the JWT,
exercises /v1/sediment/stream end-to-end for each case, parses the router
event for actual intent, captures the first ~60 chars of the answer for
human eyeball.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

PROD_API = "https://hypeproof-sediment.fly.dev"


def jwt() -> str:
    p = Path.home() / "Library/Application Support/sediment/credentials.json"
    if not p.exists():
        sys.exit(f"credentials missing: {p}")
    d = json.loads(p.read_text())
    return list(d["accounts"].values())[0]


def post(path: str, body: dict, token: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        PROD_API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


CASES = [
    # (expected_intent, query)
    ("meta",      "지금 볼트에 몇 개의 artifact가 있어?"),
    ("meta",      "How many notes do we have?"),
    ("meta",      "총 몇 개?"),
    ("meta",      "이번 달 새로 생긴 게 몇 개?"),
    ("freshness", "가장 최근의 볼트는 언제꺼야"),
    ("freshness", "가장최근의 볼트는 언제꺼야"),
    ("freshness", "최신 칼럼 알려줘"),
    ("freshness", "latest research"),
    ("freshness", "yesterday's notes"),
    ("freshness", "어제 무슨 일 있었어?"),
    ("library",   "라이언이 4월에 쓴 mirror-loop 칼럼"),
    ("library",   "PHILOSOPHY.md 내용 알려줘"),
    ("library",   "memory consolidation 관련 리서치 보여줘"),
    ("member",    "ryan은 누구야?"),
    ("member",    "who is JY?"),
    ("decision",  "최근 결정된 5/5 파일럿 관련 액션"),
    ("decision",  "결정 사항 보여줘"),
    ("freshness", "최신 결정"),
]


def run_one(token: str, query: str) -> tuple[str, str]:
    """Return (intent, first_chars_of_answer)."""
    try:
        conv_body = post("/api/v1/conversations", {"title": "golden"}, token, timeout=10)
        conv_id = json.loads(conv_body)["id"]
    except Exception as e:
        return ("?", f"(conv error: {e})")

    try:
        sse = post(
            "/v1/sediment/stream",
            {"conv_id": conv_id, "query": query},
            token,
            timeout=60,
        )
    except Exception as e:
        return ("?", f"(stream error: {e})")

    # Parse intent from router event
    intent_match = re.search(
        r'data:\s*(\{[^\n]*"step"\s*:\s*"router"[^\n]*\})', sse
    )
    intent = "?"
    if intent_match:
        try:
            intent = json.loads(intent_match.group(1))["metadata"]["intent"]
        except Exception:
            pass

    # Concatenate delta answer tokens
    deltas = re.findall(r'event: delta\ndata:\s*(\{[^\n]*\})', sse)
    answer_parts: list[str] = []
    for d in deltas:
        try:
            v = json.loads(d).get("v", "")
            if isinstance(v, str):
                answer_parts.append(v)
        except Exception:
            pass
    answer = "".join(answer_parts).strip().replace("\n", " ")
    return (intent, answer[:80])


def main() -> int:
    tok = jwt()
    pass_count = 0
    fail_count = 0
    print("=" * 90)
    print(f"Running {len(CASES)} golden cases against PROD ({PROD_API})")
    print("=" * 90)
    for expected, query in CASES:
        actual, preview = run_one(tok, query)
        ok = actual == expected
        mark = "✓" if ok else "✗"
        if ok:
            pass_count += 1
        else:
            fail_count += 1
        print(
            f"  {mark} {'PASS' if ok else 'FAIL'}  "
            f"exp={expected:<10}  got={actual:<10}  | {query[:40]:<40}  "
            f"→ {preview}"
        )
        time.sleep(0.5)
    print("=" * 90)
    print(f"  {pass_count} passed,  {fail_count} failed,  {len(CASES)} total")
    print("=" * 90)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
