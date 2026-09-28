#!/usr/bin/env python3
"""lint-design-tokens.py — guard the "Editorial Archive" design system against
token drift in the Sediment frontend.

Background
----------
`frontend/app/globals.css` defines the whole visual identity as Tailwind v4
`@theme` tokens (paper / ink / rule / accent / ochre / sage + 3 font families).
Pages that bypass those tokens — a bare `border` (which inherits `currentColor`
in Tailwind v4), a raw `bg-red-50`, a hand-typed `#8b3a2c` — drift silently:
nothing errors, the page just stops looking like the product. The 2026-05
"Editorial Archive" refresh (#76) landed on layout/home/chat but left
library/members/admin/onboard/pricing on pre-refresh styling, so the drift is
already real and measurable.

This is the L1 (deterministic) layer of the design harness. It is deliberately
modelled on `lint-sql-cast.sh`: one rule class, hard block in `ai-commit.sh
gate`, and a documented "why" so the rule survives the person who wrote it.

Design decisions worth keeping
------------------------------
1. The token allowlist is PARSED FROM `globals.css`, never hand-maintained.
   Add `--color-scrim` to `@theme` and this linter learns it for free. A
   hand-kept allowlist rots the first time someone adds a token.

2. Rules run against the class set that lands on ONE ELEMENT, not against the
   raw `className` string. `components/ui.tsx` writes
   `` `... border ... ${tones[tone]}` `` where the *variant* supplies
   `border-rule-2` — a per-string grep calls that a violation and is wrong.
   False positives are how linters get switched off, so unresolvable dynamic
   composition is skipped, not flagged (see `_border_color_anywhere`).

3. Arbitrary hex is split into two verdicts. A hex that equals an existing
   token is `hex-duplicate` (use the token). A hex that matches nothing is
   `hex-missing-token` — that is a signal the token set is INCOMPLETE, and the
   fix is to add a token, not to scold the author. Conflating the two teaches
   people to reach for `eslint-disable`.

4. Ratcheting baseline. There is pre-existing drift; blocking every file on day
   one just gets the gate bypassed. `harness/design/baseline.json` records the
   per-file count that is tolerated. Counts may only go DOWN.

Usage
-----
    python3 lint-design-tokens.py                  # scan, compare to baseline
    python3 lint-design-tokens.py --json           # machine-readable report
    python3 lint-design-tokens.py --update-baseline  # ratchet down after fixes
    python3 lint-design-tokens.py <file> [<file>]  # scan specific files

Exit codes
----------
    0 — at or below baseline
    1 — violations above baseline (or baseline drifted upward)
    2 — usage / environment error
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
GLOBALS_CSS = REPO_ROOT / "frontend" / "app" / "globals.css"
SCAN_ROOT = REPO_ROOT / "frontend" / "app"
BASELINE_PATH = REPO_ROOT / "harness" / "design" / "baseline.json"

# `components/ui.tsx` *is* the primitive layer — it is supposed to look like a
# Surface, so the "you reimplemented Surface" rule must not fire on it.
PRIMITIVE_SOURCE = "sediment/components/ui.tsx"

# Tailwind's built-in palette. Using any of these means bypassing @theme.
RAW_PALETTE = (
    "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|"
    "teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black"
)
COLOR_PREFIXES = (
    "bg|text|border|ring|decoration|divide|outline|shadow|accent|caret|fill|"
    "stroke|placeholder|from|via|to"
)
RE_RAW_PALETTE = re.compile(
    rf"(?<![-\w])(?:{COLOR_PREFIXES})-(?:{RAW_PALETTE})(?:-\d{{2,3}})?(?![-\w])"
)
RE_ARB_HEX = re.compile(r"\[#([0-9a-fA-F]{3,8})\]")
RE_BORDER_SIDE = r"(?:t|r|b|l|x|y|s|e)"

# Colors that are legitimately not @theme tokens.
KEYWORD_COLORS = {"transparent", "current", "inherit", "none", "auto"}


# ────────────────────────────── token parsing ──────────────────────────────


def parse_theme_tokens(css_path: pathlib.Path) -> tuple[dict[str, str], set[str]]:
    """Extract `--color-*` / `--font-*` from the `@theme { ... }` block.

    Returns ({token_suffix: normalized_hex}, {font_token_suffix}).
    """
    if not css_path.exists():
        sys.stderr.write(f"[lint-design-tokens] cannot read {css_path}\n")
        sys.exit(2)
    src = css_path.read_text()
    start = src.find("@theme")
    if start == -1:
        sys.stderr.write(
            f"[lint-design-tokens] no @theme block in {css_path} — the design "
            "system contract is missing; refusing to lint against nothing.\n"
        )
        sys.exit(2)
    depth, i, body_start = 0, src.find("{", start), None
    body_start = i + 1
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = src[body_start:i]

    colors: dict[str, str] = {}
    for name, value in re.findall(r"--color-([\w-]+)\s*:\s*([^;]+);", body):
        colors[name] = _norm_hex(value.strip())
    fonts = {name for name, _ in re.findall(r"--font-([\w-]+)\s*:\s*([^;]+);", body)}
    return colors, fonts


def _norm_hex(value: str) -> str:
    """#ABC -> #aabbcc so `[#8b3a2c]` can be matched against a token value."""
    v = value.strip().lower()
    m = re.match(r"^#([0-9a-f]{3})$", v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    return v


# ─────────────────────────── className extraction ───────────────────────────


def extract_class_blobs(src: str) -> list[tuple[int, str]]:
    """Find every `className=...` value with its 1-indexed line number."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"className=", src):
        i = m.end()
        line = src.count("\n", 0, m.start()) + 1
        if i >= len(src):
            continue
        if src[i] == '"':
            end = src.find('"', i + 1)
            if end != -1:
                out.append((line, src[i : end + 1]))
        elif src[i] == "{":
            depth, j = 0, i
            while j < len(src):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append((line, src[i : j + 1]))
    return out


def _is_unresolvable(expr: str) -> bool:
    """Can this `${...}` contribute classes we cannot see?

    `on ? "a" : "b"` is fully enumerated — the leftover identifier is only the
    *condition*, which contributes no classes. `tones[tone]`, `cn(x)` and
    `badge.cls` reach for strings defined elsewhere and genuinely are opaque.
    Splitting on the first ?/&&/|| separates the condition from the value side
    so a boolean guard does not make the whole className "dynamic" — which
    would silently suppress real findings via the leniency path in `scan()`.
    """
    rest = re.sub(r'"[^"]*"|\'[^\']*\'', "", expr)
    values = re.split(r"\?|&&|\|\|", rest, maxsplit=1)[-1]
    return bool(re.search(r"[\[(.]", rest) or re.search(r"[A-Za-z_$]", values))


def candidates_for(blob: str) -> tuple[list[str], bool]:
    """Split one className value into the class sets that can land on the element.

    A template literal's static text is the base every variant shares; each
    string literal found inside `${...}` (or each branch of a bare ternary) is
    unioned onto that base as its own candidate. Keeping the base and the
    variant together is what stops the `ui.tsx` false positive; keeping the
    branches apart is what stops one branch masking a defect in the other.

    Returns (candidate_class_strings, has_unresolved_dynamic).
    """
    inner = blob
    if blob.startswith("{") and blob.endswith("}"):
        inner = blob[1:-1]
    inner = inner.strip()

    # Static text of a template literal = everything outside ${...}
    base_parts: list[str] = []
    variant_parts: list[str] = []
    has_dynamic = False

    if inner.startswith("`") and inner.endswith("`"):
        body = inner[1:-1]
        pos, buf = 0, []
        while pos < len(body):
            k = body.find("${", pos)
            if k == -1:
                buf.append(body[pos:])
                break
            buf.append(body[pos:k])
            depth, j = 0, k + 1
            while j < len(body):
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            expr = body[k + 2 : j]
            lits = re.findall(r'"([^"]*)"|\'([^\']*)\'', expr)
            if lits:
                variant_parts.extend(a or b for a, b in lits)
            if _is_unresolvable(expr):
                has_dynamic = True
            pos = j + 1
        base_parts.append(" ".join(buf))
    else:
        lits = re.findall(r'"([^"]*)"|\'([^\']*)\'', inner)
        if lits:
            variant_parts.extend(a or b for a, b in lits)
        if _is_unresolvable(inner):
            has_dynamic = True

    base = " ".join(p for p in base_parts if p).strip()
    if not variant_parts:
        return ([base] if base else []), has_dynamic
    return [f"{base} {v}".strip() for v in variant_parts], has_dynamic


# ───────────────────────────────── rules ─────────────────────────────────


class Linter:
    def __init__(self, colors: dict[str, str], fonts: set[str]):
        self.colors = colors
        self.fonts = fonts
        self.hex_to_token = {v: k for k, v in colors.items()}
        tok = "|".join(sorted(map(re.escape, colors), key=len, reverse=True))
        # `border-rule-2` is a color; `border-l-2` is a width. Only the parsed
        # token list can tell them apart, so the alternation is built from it.
        self.re_border_color = re.compile(
            rf"(?<![-\w])border-(?:{RE_BORDER_SIDE}-)?(?:{tok}|"
            rf"{'|'.join(KEYWORD_COLORS)})(?:/\d{{1,3}})?(?![-\w])"
        )
        self.re_border_width = re.compile(
            rf"(?<![-\w])border(?:-{RE_BORDER_SIDE})?(?:-(?:\d+|\[[^\]]+\]))?(?![-\w])"
        )

    def _is_border_width(self, cls: str) -> bool:
        if self.re_border_color.fullmatch(cls):
            return False
        return bool(self.re_border_width.fullmatch(cls))

    def check(self, classes: str, *, rel: str, allow_dynamic_border: bool) -> list[tuple[str, str]]:
        """Return [(rule_id, detail)] for one element's class set."""
        found: list[tuple[str, str]] = []
        toks = classes.split()
        bare = [t.split(":")[-1] for t in toks]  # drop md: / hover: / focus: prefixes

        has_border_w = any(self._is_border_width(t) for t in bare)
        has_border_c = bool(self.re_border_color.search(classes))
        if has_border_w and not has_border_c and not allow_dynamic_border:
            found.append(("border-no-color", "border width without a token border color"))

        for t in bare:
            if t == "rounded":
                found.append(("bare-rounded", "rounded (use rounded-sm/md/lg)"))

        for m in RE_RAW_PALETTE.finditer(classes):
            found.append(("raw-palette", m.group(0)))

        for m in RE_ARB_HEX.finditer(classes):
            h = _norm_hex("#" + m.group(1))
            token = self.hex_to_token.get(h)
            if token:
                found.append(("hex-duplicate", f"{m.group(0)} == --color-{token}"))
            else:
                found.append(("hex-missing-token", m.group(0)))

        if rel != PRIMITIVE_SOURCE:
            # Match <Surface>'s EXACT recipe (rounded-md), not "any card-ish box".
            # A modal that deliberately picks rounded-lg + its own elevation is a
            # considered divergence, not drift — flagging it would train people to
            # ignore this rule. `device/page.tsx` reproduces Surface's shadow value
            # verbatim; that is the shape this rule exists to catch.
            has_md = "rounded-md" in bare
            has_shadow = any(t.startswith("shadow") for t in bare)
            has_card = any(t in ("bg-card", "bg-card-2") for t in bare)
            if has_md and has_shadow and has_card and (has_border_w or has_border_c):
                found.append(("primitive-reimpl", "rounded-md+border+bg-card+shadow — use <Surface>"))
        return found


FIX_HINTS = {
    "border-no-color": "add a token color (border-rule / border-rule-2 / border-accent). "
    "In Tailwind v4 a bare `border` inherits currentColor.",
    "bare-rounded": "use an explicit radius step: rounded-sm | rounded-md | rounded-lg.",
    "raw-palette": "replace with an @theme token (ink / ink-2 / accent / ochre / sage / claret-soft).",
    "hex-duplicate": "the value already exists as a token — reference the token instead.",
    "hex-missing-token": "the token set is incomplete — add the color to @theme in globals.css, "
    "then use it. Do not inline the hex.",
    "primitive-reimpl": "use <Surface> from app/sediment/components/ui.tsx instead of "
    "re-deriving its class recipe.",
}


# ───────────────────────────────── driver ─────────────────────────────────


def scan(files: list[pathlib.Path], linter: Linter) -> dict[str, list[dict]]:
    findings: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        if f.suffix != ".tsx" or not f.is_file():
            continue
        rel = f.relative_to(SCAN_ROOT).as_posix()
        src = f.read_text()
        # Leniency escape hatch: when a className is composed dynamically and
        # the file *does* supply token border colors elsewhere, we cannot prove
        # the element lacks one. Skip rather than guess wrong.
        border_anywhere = bool(linter.re_border_color.search(src))
        seen: set[tuple[int, str, str]] = set()
        for line, blob in extract_class_blobs(src):
            cands, has_dynamic = candidates_for(blob)
            allow = has_dynamic and border_anywhere
            for cand in cands:
                for rule, detail in linter.check(cand, rel=rel, allow_dynamic_border=allow):
                    key = (line, rule, detail)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings[rel].append({"line": line, "rule": rule, "detail": detail})
    return findings


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text()).get("files", {})


def write_baseline(counts: dict[str, int]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(
            {
                "_comment": (
                    "Ratchet for lint-design-tokens.py. Per-file tolerated violation "
                    "counts. These may only go DOWN — the gate fails if a file exceeds "
                    "its number. After fixing drift run "
                    "`make lint-design-update` and commit the lowered baseline."
                ),
                "files": dict(sorted(counts.items())),
                "total": sum(counts.values()),
            },
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("files", nargs="*", help="specific .tsx files (default: whole app)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--update-baseline", action="store_true", help="ratchet the baseline")
    args = ap.parse_args()

    colors, fonts = parse_theme_tokens(GLOBALS_CSS)
    linter = Linter(colors, fonts)

    if args.files:
        targets = [pathlib.Path(f).resolve() for f in args.files]
        targets = [t for t in targets if t.is_file() and SCAN_ROOT in t.parents]
        partial = True
    else:
        targets = sorted(SCAN_ROOT.rglob("*.tsx"))
        partial = False

    findings = scan(targets, linter)
    counts = {k: len(v) for k, v in findings.items()}

    if args.update_baseline:
        if partial:
            sys.stderr.write("[lint-design-tokens] --update-baseline needs a full scan\n")
            return 2
        write_baseline(counts)
        print(f"[lint-design-tokens] baseline written: {sum(counts.values())} tolerated "
              f"across {len(counts)} files -> {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    baseline = load_baseline()

    if args.json:
        print(json.dumps({
            "tokens": {"colors": sorted(colors), "fonts": sorted(fonts)},
            "findings": findings,
            "counts": counts,
            "baseline": baseline,
        }, indent=2))

    regressions = {f: (n, baseline.get(f, 0)) for f, n in counts.items() if n > baseline.get(f, 0)}

    if not args.json:
        if findings:
            for rel in sorted(findings):
                allowed = baseline.get(rel, 0)
                status = "OVER" if counts[rel] > allowed else "ok"
                print(f"\n── {rel}  ({counts[rel]} found / {allowed} allowed) [{status}]")
                for v in sorted(findings[rel], key=lambda d: d["line"]):
                    print(f"   :{v['line']:<5} {v['rule']:<20} {v['detail']}")
        total = sum(counts.values())
        print(f"\n[lint-design-tokens] {total} violations in {len(counts)} files "
              f"(baseline total {sum(baseline.values())}); "
              f"tokens: {len(colors)} colors, {len(fonts)} fonts")

    if regressions:
        sys.stderr.write("\n[lint-design-tokens] FAIL — above baseline:\n")
        rules_hit = set()
        for f, (now, alw) in sorted(regressions.items()):
            sys.stderr.write(f"  {f}: {now} > {alw}\n")
            rules_hit.update(v["rule"] for v in findings[f])
        sys.stderr.write("\nHow to fix:\n")
        for r in sorted(rules_hit):
            sys.stderr.write(f"  {r}: {FIX_HINTS.get(r, '')}\n")
        sys.stderr.write(
            "\nDesign tokens live in frontend/app/globals.css (@theme). Shared "
            "primitives live in frontend/app/sediment/components/ui.tsx.\n"
            "If you genuinely fixed drift elsewhere, run `make lint-design-update`.\n"
        )
        return 1

    if not partial and not args.json:
        stale = {f: n for f, n in baseline.items() if counts.get(f, 0) < n}
        if stale:
            print("[lint-design-tokens] baseline is loose (drift was fixed but not ratcheted):")
            for f, n in sorted(stale.items()):
                print(f"    {f}: baseline {n} -> actual {counts.get(f, 0)}  (run make lint-design-update)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
