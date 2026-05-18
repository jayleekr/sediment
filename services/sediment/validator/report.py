"""Report writer — JSON + Markdown + console."""
from __future__ import annotations
import json
from pathlib import Path
from .types import PhaseReport, CheckResult


def write_reports(report: PhaseReport, base_path: Path) -> None:
    """Write JSON + Markdown to base_path.json / base_path.md."""
    base_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = base_path.with_suffix(".json")
    md_path = base_path.with_suffix(".md")
    json_path.write_text(json.dumps(report.to_dict(), default=str, indent=2, ensure_ascii=False))
    md_path.write_text(_render_markdown(report))


def _render_markdown(report: PhaseReport) -> str:
    layers = sorted({r.layer for r in report.results})
    by_layer: dict[str, list[CheckResult]] = {L: [] for L in layers}
    for r in report.results:
        by_layer[r.layer].append(r)

    lines: list[str] = []
    status = "✅ PASS" if report.passes() else "❌ FAIL"
    lines.append(f"# {report.phase_id} — {report.phase_name} [{status}]")
    lines.append("")
    lines.append(f"- Iteration: **{report.iteration}**")
    lines.append(f"- Timestamp: {report.timestamp}")
    lines.append(f"- Elapsed: {report.elapsed_s}s")
    lines.append(f"- Score: **{report.score} / {report.max_score}** ({report.score_pct:.1f}%)")
    lines.append(f"- Blockers: **{report.blockers_passed} / {report.blockers_total}**")
    lines.append("")

    lines.append("## Layer breakdown")
    lines.append("")
    lines.append("| Layer | Passed | Total | % |")
    lines.append("|---|---|---|---|")
    for L in layers:
        items = by_layer[L]
        passed = sum(1 for r in items if r.passed)
        lines.append(f"| {L} | {passed} | {len(items)} | {report.layer_pct(L):.0f}% |")
    lines.append("")

    failures = report.failures
    if failures:
        lines.append("## Failures")
        lines.append("")
        for r in failures:
            badge = {"blocker": "🔴", "major": "🟡", "minor": "🟢"}[r.severity]
            lines.append(f"### {badge} {r.id} — {r.title}")
            lines.append("")
            lines.append(f"- Layer: `{r.layer}` · Severity: `{r.severity}` · Duration: {r.duration_ms}ms")
            if r.expected is not None:
                lines.append(f"- Expected: `{r.expected}`")
            if r.actual is not None:
                lines.append(f"- Actual: `{r.actual}`")
            if r.message:
                lines.append(f"- Message: {r.message}")
            if r.artifacts:
                lines.append("- Artifacts:")
                for a in r.artifacts:
                    lines.append(f"  - `{a}`")
            lines.append("")

    lines.append("## All checks")
    lines.append("")
    lines.append("| ID | Layer | Severity | Status | Duration |")
    lines.append("|---|---|---|---|---|")
    for r in report.results:
        st = "✅" if r.passed else "❌"
        lines.append(f"| {r.id} | {r.layer} | {r.severity} | {st} | {r.duration_ms}ms |")
    lines.append("")

    return "\n".join(lines)


def print_console(report: PhaseReport) -> None:
    """Pretty-print a one-line summary to stdout."""
    status = "PASS" if report.passes() else "FAIL"
    print()
    print("─" * 76)
    print(f" {report.phase_id} · iter {report.iteration} · {status}")
    print(f" {report.score}/{report.max_score} weight ({report.score_pct:.1f}%) · "
          f"blockers {report.blockers_passed}/{report.blockers_total} · {report.elapsed_s}s")
    print("─" * 76)

    layers = sorted({r.layer for r in report.results})
    for L in layers:
        items = [r for r in report.results if r.layer == L]
        passed = sum(1 for r in items if r.passed)
        bar = "█" * int(report.layer_pct(L) / 10) + "░" * (10 - int(report.layer_pct(L) / 10))
        print(f"   {L:<4} {bar} {passed}/{len(items)} ({report.layer_pct(L):.0f}%)")

    failures = report.failures
    if failures:
        print(f"\n   Failures ({len(failures)}):")
        for r in failures[:8]:
            badge = {"blocker": "BLOCK", "major": "MAJOR", "minor": "minor"}[r.severity]
            print(f"     [{badge}] {r.id} — {r.title}")
            if r.message:
                msg = r.message.replace("\n", " ")[:120]
                print(f"            → {msg}")
        if len(failures) > 8:
            print(f"     ... and {len(failures) - 8} more (see report.md)")
    print()
