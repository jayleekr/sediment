"""Dogfood digest — content producer for #sediment-dogfood.

Pure formatter over p5_activation + vault freshness. Produces a JSON content
block; **Mother posts it** (Sediment never sends to Discord directly — project
rule). Stateless and offline-honest: if the DB is absent, p5_activation
degrades with flags and freshness reports unknown — it never fabricates a
green number.

Run:
    .venv/bin/python -m scripts.dogfood_digest          # daily
    .venv/bin/python -m scripts.dogfood_digest --weekly  # + fix-log health
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys

from sqlalchemy import text

from lab_lib.db import service_session
from validator.checks.p5_activation import OUT_DIR, compute_activation


async def _freshness() -> dict:
    try:
        async with service_session() as s:
            r = await s.execute(text("""
                SELECT ts, payload FROM events
                WHERE kind = 'vault.ingest' ORDER BY ts DESC LIMIT 1
            """))
            row = r.first()
    except Exception as e:
        return {"text": "vault: unknown (DB unreachable)", "stale": True, "err": str(e)}
    if not row:
        return {"text": "vault: never ingested ⚠", "stale": True}
    age = int((_dt.datetime.now(_dt.timezone.utc) - row[0]).total_seconds())
    h = age / 3600
    label = f"{int(age/60)}m" if age < 3600 else (f"{h:.0f}h" if h < 24 else f"{h/24:.0f}d")
    stale = age > 24 * 3600
    return {"text": f"vault updated {label} ago" + (" ⚠ STALE" if stale else " ●"),
            "stale": stale}


async def build_digest(weekly: bool = False) -> dict:
    act = await compute_activation()
    fresh = await _freshness()
    dist = act["ladder_distribution"]
    ladder = " ".join(f"{k}:{dist[k]}" for k in ("S0", "S1", "S2", "S3", "S4", "S5"))

    blocks = [
        f"**Sediment dogfood — {_dt.date.today().isoformat()}**",
        f"ladder  {ladder}",
        f"S3+ {act['s3plus_count']} · S4+ {act['s4plus_count']} · verdict **{act['adoption_verdict']}**",
        fresh["text"],
    ]
    if act["flags"]:
        blocks.append("⚠ honesty flags: " + "; ".join(act["flags"][:4]))
    if weekly:
        blocks.append(
            f"loop health — bug-fix median: {act.get('median_bug_fix_hours') or 'n/a'}h · "
            f"closure: {act.get('fix_log_closure_rate') or 'n/a'}"
        )

    return {
        "channel": "#sediment-dogfood",
        "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "cadence": "weekly" if weekly else "daily",
        "blocks": blocks,
        "raw": {"activation": act, "freshness": fresh},
        "note": "produced by Sediment; Mother posts (Sediment never sends).",
    }


def main():
    weekly = "--weekly" in sys.argv
    d = asyncio.run(build_digest(weekly))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "digest-weekly" if weekly else "digest"
    (OUT_DIR / f"{_dt.date.today().isoformat()}.{suffix}.json").write_text(
        json.dumps(d, indent=2, default=str)
    )
    json.dump(d, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
