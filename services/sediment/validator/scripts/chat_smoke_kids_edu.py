"""End-to-end chat smoke for the kids-edu tenant.

Validates the entire post-ingest pipeline against prod:
  1. mint a kids-edu dev token (Jay's email is seeded as admin)
  2. create a conversation
  3. POST /v1/sediment/stream with the canonical prompt
  4. parse SSE, collect citations, await [DONE]
  5. assert >= 1 citation AND at least one cites the AI Native assets file

Pass = exits 0. Fail = exits 2 with a diff describing what was missing.
No browser, no Playwright — pure HTTP. Run anywhere.

Env:
  SEDIMENT_API       https://hypeproof-sediment.fly.dev (default)
  KIDS_EDU_EMAIL     jayleekr0125@gmail.com (default — seeded admin)
  KIDS_EDU_TENANT    kids-edu (default)
  KIDS_EDU_TOKEN     pre-minted kids-edu JWT; skips /dev-token when set
  SEDIMENT_E2E_JWT   fallback pre-minted JWT name used by other E2E scripts
  CHAT_QUERY         "AI Native 마인드 7종 설명해줘" (default)
  EXPECT_REF         partial substring required in at least one citation ref
                     (default: "ai-native-assets")
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

import httpx

# parents[2] is services/sediment/ — sibling to lab_lib + scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._test_helpers import ask_question  # noqa: E402

API = os.environ.get("SEDIMENT_API", "https://hypeproof-sediment.fly.dev")
EMAIL = os.environ.get("KIDS_EDU_EMAIL", "jayleekr0125@gmail.com")
TENANT = os.environ.get("KIDS_EDU_TENANT", "kids-edu")
TOKEN = os.environ.get("KIDS_EDU_TOKEN") or os.environ.get("SEDIMENT_E2E_JWT")
QUERY = os.environ.get("CHAT_QUERY", "AI Native 마인드 7종 설명해줘")
EXPECT = os.environ.get("EXPECT_REF", "ai-native-assets")
TIMEOUT_S = float(os.environ.get("CHAT_TIMEOUT_S", "60"))


async def main() -> int:
    print("== kids-edu chat smoke ==")
    print(f"API:    {API}")
    print(f"EMAIL:  {EMAIL}")
    print(f"TENANT: {TENANT}")
    print(f"TOKEN:  {'provided' if TOKEN else 'dev-token'}")
    print(f"QUERY:  {QUERY!r}")
    print(f"EXPECT: ref contains {EXPECT!r}")
    print()
    try:
        result = await ask_question(
            api=API, email=EMAIL, tenant_slug=TENANT, token=TOKEN, query=QUERY,
            title_base="kids-edu-smoke", timeout_s=TIMEOUT_S,
        )
    except httpx.HTTPStatusError as e:
        print(f"FATAL http {e.response.status_code}: {e.response.text[:500]}",
              file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    cits = result["citations"]
    ans = result["answer"]
    print(f"conv_id:   {result['conv_id']}")
    print(f"events:    {result['events']}")
    print(f"intent:    {result.get('intent')}")
    print(f"citations: {len(cits)}")
    for i, c in enumerate(cits[:5], 1):
        print(f"  [{i}] {c.get('ref','?')} ({c.get('type','?')})")
    if len(cits) > 5:
        print(f"  ...({len(cits)-5} more)")
    print(f"\nanswer (first 240 chars):\n  {ans[:240]!r}")

    # Assertions
    if len(cits) == 0:
        print("\nFAIL: zero citations — retrieval landed nothing", file=sys.stderr)
        return 2
    matched = [c for c in cits if EXPECT in (c.get("ref") or "")]
    if not matched:
        print(f"\nFAIL: no citation contained {EXPECT!r}", file=sys.stderr)
        print("    got refs:", [c.get("ref") for c in cits], file=sys.stderr)
        return 2
    if not ans.strip():
        print("\nFAIL: empty answer body", file=sys.stderr)
        return 2

    print(f"\nOK: {len(cits)} citations, {len(matched)} match {EXPECT!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
