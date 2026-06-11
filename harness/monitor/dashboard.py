"""Static HTML dashboard generator for Ralph + validator state.

Run periodically (e.g., every 30s via cron or watch):
  python3 products/sediment/harness/monitor/dashboard.py

Generates: output/ralph-dashboard.html (open in browser, auto-refreshes via meta tag)
"""
from __future__ import annotations
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RALPH_DIR = REPO_ROOT / "harness" / "ralph"
VALID_DIR = REPO_ROOT / "output" / "validation"
OUT_PATH = REPO_ROOT / "output" / "ralph-dashboard.html"


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _read_text(p: Path, tail: int = 0) -> str:
    if not p.exists():
        return ""
    txt = p.read_text(errors="ignore")
    if tail:
        return "\n".join(txt.splitlines()[-tail:])
    return txt


def _todo_counts(p: Path) -> tuple[int, int]:
    if not p.exists():
        return 0, 0
    txt = p.read_text(errors="ignore")
    return txt.count("- [ ]"), txt.count("- [x]")


def _latest_validation() -> dict | None:
    candidates = list(VALID_DIR.glob("*-latest.json"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return _read_json(latest)


def _loop_histories() -> list[dict]:
    rows: list[dict] = []
    for hist in VALID_DIR.glob("loop-*-*/history.csv"):
        try:
            with hist.open() as f:
                reader = list(csv.DictReader(f))
            if not reader:
                continue
            phase = hist.parent.name.split("-")[1]
            rows.append({
                "phase": phase,
                "dir": str(hist.parent),
                "iters": len(reader),
                "last_pct": reader[-1].get("pct", "?"),
                "last_blockers": f"{reader[-1].get('blockers_passed', '?')}/{reader[-1].get('blockers_total', '?')}",
                "passed": reader[-1].get("passed", "?"),
                "history": reader[-20:],
            })
        except Exception:
            continue
    return rows


def render() -> str:
    state = _read_json(RALPH_DIR / "STATE.json")
    todo_open, todo_done = _todo_counts(RALPH_DIR / "TODO.md")
    journal_tail = _read_text(RALPH_DIR / "JOURNAL.md", tail=15)
    latest = _latest_validation()
    loops = _loop_histories()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    rows_html = ""
    for L in loops[-5:]:
        bars = "".join(
            f'<span class="bar" style="height:{min(int(float(r.get("pct", 0))), 100)}px"></span>'
            for r in L["history"]
        )
        rows_html += f"""
        <tr>
          <td>{L['phase']}</td>
          <td>{L['iters']}</td>
          <td>{L['last_pct']}%</td>
          <td>{L['last_blockers']}</td>
          <td>{'✅' if L['passed'] == 'True' else '❌'}</td>
          <td class="bars">{bars}</td>
        </tr>"""

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>Sediment Ralph Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background:#0b0d12; color:#e2e8f0; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 16px; }}
  .grid {{ display:grid; grid-template-columns: 1fr 1fr 1fr; gap:16px; margin-bottom:24px; }}
  .card {{ background:#161922; border:1px solid #2a2f3d; padding:16px; border-radius:8px; }}
  .num {{ font-size:32px; font-weight:600; color:#7dd3fc; }}
  .lbl {{ color:#94a3b8; font-size:12px; text-transform:uppercase; letter-spacing:1px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:6px 12px; border-bottom:1px solid #2a2f3d; }}
  pre {{ background:#0b0d12; padding:12px; border-radius:6px; font-size:12px; overflow:auto; max-height:280px; }}
  .bars {{ display:flex; align-items:flex-end; height:60px; gap:2px; }}
  .bar {{ background:#7dd3fc; width:6px; }}
  .ok {{ color:#86efac; }}
  .err {{ color:#fca5a5; }}
</style>
</head><body>
<h1>Sediment — Ralph Dashboard <span style="color:#64748b;font-size:13px;">{now}</span></h1>

<div class="grid">
  <div class="card">
    <div class="lbl">Ralph iter</div>
    <div class="num">{state.get('iteration', 0)} / {state.get('max_iterations', 200)}</div>
    <div>phase: <strong>{state.get('current_phase') or '-'}</strong></div>
    <div>status: <strong>{state.get('stop_reason') or 'running'}</strong></div>
  </div>
  <div class="card">
    <div class="lbl">Cost</div>
    <div class="num">${state.get('cumulative_cost_usd', 0):.2f}</div>
    <div>budget: ${state.get('cost_budget_usd', 100)}</div>
  </div>
  <div class="card">
    <div class="lbl">TODO</div>
    <div class="num">{todo_open}</div>
    <div>done: {todo_done}</div>
  </div>
</div>

<div class="card">
  <h3>Validation loops</h3>
  <table>
    <thead><tr><th>Phase</th><th>Iters</th><th>Score</th><th>Blockers</th><th>Pass</th><th>Curve</th></tr></thead>
    <tbody>{rows_html or '<tr><td colspan=6>(no loops yet)</td></tr>'}</tbody>
  </table>
</div>

<div style="height:16px"></div>

<div class="card">
  <h3>Latest validation</h3>
  <pre>{json.dumps(latest, indent=2, default=str) if latest else '(none)'}</pre>
</div>

<div style="height:16px"></div>

<div class="card">
  <h3>Journal tail</h3>
  <pre>{journal_tail or '(empty)'}</pre>
</div>

</body></html>"""


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render())
    print(f"wrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
