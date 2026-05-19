"""Activation Engine scorer — the LEADING funnel + S0–S5 maturity ladder.

Companion to `p5_dogfood.py` (which computes the 10 LAGGING criteria). This
module computes the activation funnel and the AIT adoption ladder defined in
`ACTIVATION_ENGINE.md` §5, emitted additively under an `activation` key — it
does NOT touch the existing criteria.

Run:
    .venv/bin/python -m validator.checks.p5_activation

Honesty contract (this is the whole point of the engine — see §1, §3a, §6):
  * `X` (the S3 owned-task query-rate threshold) is NOT hardcoded. It is read
    from a locked artifact. **Until X is locked, S3 cannot fire** and the
    adoption verdict is "cannot evaluate", never a silent pass.
  * S3(a) requires the `task_tag` query signal. If no query carries
    task_tag='owned', S3(a) is uncomputable and S3 collapses to fully
    judgement-assisted (Jay override mandatory for every S3 user, not just
    borderline) — surfaced as a flag, not hidden.
  * S3(b), the "did you grep/Drive-search instead?" self-report, is
    irreducibly manual (no instrument observes a terminal grep). Read from a
    manual file; absence → S3(b) unknown.
  * Every degradation is appended to `flags` — the score is never inflated to
    cover a missing instrument.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from lab_lib.db import service_session


# ── Robust paths (do NOT repeat p5_dogfood.py's stale parents[6] bug, which
#    assumed the pre-split monorepo layout and breaks in the extracted repo).
#    Anchor on the repo marker instead of a hardcoded parent depth. ──
def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "infra" / "init.sql").is_file():
            return p
    # Fallback: env override, else 4 levels up (services/sediment/validator/checks → root)
    env = os.environ.get("SEDIMENT_REPO_ROOT")
    return Path(env) if env else here.parents[3]


REPO_ROOT = _repo_root()
OUT_DIR = REPO_ROOT / "output" / "dogfood"

# Manual / locked artifacts (created during the dogfood, not by code)
X_LOCK_FILE = OUT_DIR / "x-threshold.json"        # {"x_per_week": <float>} — §3a, locked Week-1 exit
S3_SELFREPORT_FILE = OUT_DIR / "s3-selfreport.json"  # {"<member_id>": true|false} — Week-2 standup
PMF_POLL_FILE = OUT_DIR / "pmf-week4.json"         # {"responses":[{"member_id","level"}]} S4 (n>=6)
FIXLOG_FILE = OUT_DIR / "fixlog.json"              # {"median_fix_hours":h,"closure_rate":r} §5 loop health


def _load_json(p: Path):
    try:
        return json.loads(p.read_text()) if p.is_file() else None
    except Exception:
        return None


async def _members() -> list[dict]:
    async with service_session() as s:
        r = await s.execute(text(
            "SELECT id::text, display_name, github_login FROM members "
            "WHERE email IS NOT NULL OR github_login IS NOT NULL"
        ))
        return [{"member_id": row[0], "name": row[1], "github": row[2]} for row in r]


async def _per_user_signals() -> dict:
    """Per member: first activity ts, query days, owned-task weekly rates,
    aha (first cited answer / first cite_export), cite_export targets."""
    async with service_session() as s:
        # Query events (canonical activity; carries member_id + task_tag).
        q = await s.execute(text("""
            SELECT member_id::text,
                   min(ts)                                              AS first_q,
                   count(*)                                             AS n_q,
                   count(*) FILTER (WHERE (payload->>'n_citations')::int > 0) AS n_cited,
                   min(ts) FILTER (WHERE (payload->>'n_citations')::int > 0)  AS first_cited,
                   count(DISTINCT date(ts))                             AS active_days,
                   count(*) FILTER (WHERE payload->>'task_tag' = 'owned') AS n_owned,
                   count(DISTINCT date_trunc('week', ts))
                       FILTER (WHERE payload->>'task_tag' = 'owned')    AS owned_weeks
            FROM events
            WHERE kind = 'query' AND member_id IS NOT NULL
            GROUP BY member_id
        """))
        per_q = {row[0]: dict(
            first_q=row[1], n_q=int(row[2]), n_cited=int(row[3]),
            first_cited=row[4], active_days=int(row[5]),
            n_owned=int(row[6]), owned_weeks=int(row[7] or 0),
        ) for row in q}

        # cite_export events (magic-number proxy + S5 hand-off signal).
        ce = await s.execute(text("""
            SELECT member_id::text,
                   count(*)                                                       AS n_ce,
                   min(ts)                                                        AS first_ce,
                   count(*) FILTER (WHERE payload->>'target' IN ('@teammate','thread-share')) AS n_handoff
            FROM events
            WHERE kind = 'cite_export' AND member_id IS NOT NULL
            GROUP BY member_id
        """))
        per_ce = {row[0]: dict(n_ce=int(row[1]), first_ce=row[2], n_handoff=int(row[3]))
                  for row in ce}
    return {"q": per_q, "ce": per_ce}


def _rung(sig: dict, ce: dict, x_per_week, self_report, flags: list[str]) -> str:
    """S0..S5 per §5. Honest: S3 cannot pass without X locked AND a true
    self-report; missing instruments cap the rung, never inflate it."""
    n_q = sig.get("n_q", 0)
    aha = (sig.get("n_cited", 0) > 0) or (ce.get("n_ce", 0) > 0)
    if not aha:
        return "S1" if n_q > 0 else "S0"

    # S5 (auto): an observed hand-off.
    if ce.get("n_handoff", 0) >= 1:
        rung = "S5"
    else:
        rung = "S1"

    # S2 (auto-ish): >=3 distinct active days. "unprompted" (no trigger-bot
    # nudge that session) is not yet distinguishable — flagged, not asserted.
    if sig.get("active_days", 0) >= 3:
        rung = _max_rung(rung, "S2")

    # S3 (hybrid, NOT auto): owned-task rate >= X for >=2 weeks AND self-report.
    if x_per_week is None:
        flags.append("S3 not evaluable: X threshold not locked (§3a) — gate cannot fire")
    else:
        owned_weeks = sig.get("owned_weeks", 0)
        n_owned = sig.get("n_owned", 0)
        rate_ok = owned_weeks >= 2 and (n_owned / max(owned_weeks, 1)) >= x_per_week
        if sig.get("n_owned", 0) == 0:
            flags.append("S3(a) uncomputable for a user: no task_tag='owned' queries "
                         "— S3 fully judgement-assisted (Jay override mandatory)")
        sr = self_report  # True = old path NOT used (good); False/None otherwise
        if rate_ok and sr is True:
            rung = _max_rung(rung, "S3")
        elif rate_ok and sr is None:
            flags.append("S3(b) self-report missing for a qualifying user — "
                         "rung held at S2 pending standup")
    return rung


_ORDER = {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}


def _max_rung(a: str, b: str) -> str:
    return a if _ORDER[a] >= _ORDER[b] else b


async def compute_activation() -> dict:
    """The `activation` block (ACTIVATION_ENGINE.md §5). Additive — callers
    merge this under output/dogfood/<date>.json['activation']."""
    flags: list[str] = []

    members = await _members()
    sig = await _per_user_signals()
    perq, perce = sig["q"], sig["ce"]

    x_doc = _load_json(X_LOCK_FILE)
    x_per_week = x_doc.get("x_per_week") if x_doc else None
    if x_per_week is None:
        flags.append("X not locked → S3 cannot fire → §6 verdict = CANNOT EVALUATE "
                     "(this is correct behaviour, not a failure)")

    selfrep = _load_json(S3_SELFREPORT_FILE) or {}
    pmf = _load_json(PMF_POLL_FILE)
    fixlog = _load_json(FIXLOG_FILE)

    per_user = {}
    dist = {k: 0 for k in _ORDER}
    for m in members:
        mid = m["member_id"]
        s = perq.get(mid, {})
        c = perce.get(mid, {})
        rung = _rung(s, c, x_per_week, selfrep.get(mid), flags)

        # S4 (auto from PMF poll, valid only if n>=6).
        if pmf and isinstance(pmf.get("responses"), list):
            if len(pmf["responses"]) >= 6:
                mine = next((r for r in pmf["responses"] if r.get("member_id") == mid), None)
                if mine and mine.get("level") == "very_disappointed":
                    rung = _max_rung(rung, "S4")
            else:
                flags.append("PMF poll n<6 → S4 invalid → verdict CONDITIONAL pending re-poll")
        first_q = s.get("first_q")
        first_aha = c.get("first_ce") or s.get("first_cited")
        tta = None
        if first_q and first_aha:
            tta = round((first_aha - first_q).total_seconds() / 3600.0, 2)

        per_user[mid] = {
            "name": m["name"],
            "reached_aha": (s.get("n_cited", 0) > 0) or (c.get("n_ce", 0) > 0),
            "time_to_aha_hours": tta,
            "magic_number_hits": c.get("n_ce", 0),   # PROXY (clipboard not ground truth)
            "rung": rung,
            "s3_evidence": {
                "query_rate": (s.get("n_owned", 0) / s["owned_weeks"])
                              if s.get("owned_weeks") else None,
                "self_report_old_path_used": (
                    None if selfrep.get(mid) is None else (selfrep.get(mid) is False)
                ),
            },
        }
        dist[rung] += 1

    if not pmf:
        flags.append("PMF poll file absent → S4 uncomputable (manual Week-4 Discord poll)")
    if fixlog is None:
        flags.append("fixlog.json absent → loop-health (median_bug_fix_hours / "
                      "fix_log_closure_rate) not instrumented yet (§5)")
    if not any(perq.get(m["member_id"], {}).get("n_owned") for m in members):
        flags.append("NO task_tag='owned' queries anywhere → S3(a) globally "
                      "uncomputable; engine is measuring but task_tag not yet wired in UI")

    n = len(members) or 1
    s3plus = sum(1 for u in per_user.values() if _ORDER[u["rung"]] >= 3)
    s4plus = sum(1 for u in per_user.values() if _ORDER[u["rung"]] >= 4)
    if x_per_week is None:
        verdict = "CANNOT_EVALUATE (X not locked — §3a)"
    elif s3plus >= 5 and s4plus >= 3:
        verdict = "GO"
    elif s3plus >= 3:
        verdict = "CONDITIONAL"
    else:
        verdict = "NO-GO"

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "x_per_week": x_per_week,
        "ladder_distribution": dist,
        "s3plus_count": s3plus,
        "s4plus_count": s4plus,
        "adoption_verdict": verdict,
        "median_bug_fix_hours": (fixlog or {}).get("median_fix_hours"),
        "fix_log_closure_rate": (fixlog or {}).get("closure_rate"),
        "per_user": per_user,
        "flags": sorted(set(flags)),
        "note": "Leading funnel + S0–S5 ladder (§5). S3 is judgement-assisted; "
                "verdict here is advisory input to §6 — Jay's override governs.",
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(compute_activation())
    out = OUT_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.activation.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    json.dump(result, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
