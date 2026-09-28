"""Regression tests for harness/scripts/lint-design-tokens.py.

The linter's value depends entirely on it not crying wolf, and two judgement
calls carry that weight:

  1. A className composed from a base template plus a variant object
     (`components/ui.tsx`) must NOT be reported — the variant supplies the
     border color. A naive per-string grep gets this wrong, and one false
     positive is enough for a team to stop trusting the gate.

  2. `primitive-reimpl` fires on <Surface>'s exact recipe (rounded-md), not on
     any card-shaped box. The citation modal deliberately picks rounded-lg and
     its own elevation; flagging that would train people to ignore the rule.

Both are easy to "simplify" away later, so they are pinned here.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LINTER_PATH = REPO_ROOT / "harness" / "scripts" / "lint-design-tokens.py"


def _load():
    spec = importlib.util.spec_from_file_location("lint_design_tokens", LINTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lint = _load()


THEME_CSS = """
@theme {
  --color-paper:       #f1ebdd;
  --color-card:        #fcf8ef;
  --color-ink:         #221e16;
  --color-ink-3:       #8c8169;
  --color-ink-inverse: #e9e0cb;
  --color-rule:        #ddd1bb;
  --color-rule-2:      #c8b99c;
  --color-accent:      #8b3a2c;
  --color-sage:        #4f6a52;
  --color-scrim:       #1a160f;
  --font-display: "Fraunces", Georgia, serif;
  --font-mono:    "IBM Plex Mono", monospace;
}
"""


@pytest.fixture(scope="module")
def linter(tmp_path_factory):
    css = tmp_path_factory.mktemp("theme") / "globals.css"
    css.write_text(THEME_CSS)
    colors, fonts = lint.parse_theme_tokens(css)
    return lint.Linter(colors, fonts), colors, fonts


def rules(linter_obj, classes, *, rel="sediment/page.tsx", allow_dynamic=False):
    return {r for r, _ in linter_obj.check(classes, rel=rel, allow_dynamic_border=allow_dynamic)}


# ───────────────────────────── token parsing ─────────────────────────────


def test_tokens_come_from_the_theme_block_not_a_hardcoded_list(linter):
    _, colors, fonts = linter
    assert colors["accent"] == "#8b3a2c"
    assert colors["scrim"] == "#1a160f"
    assert "display" in fonts and "mono" in fonts
    # A token added to @theme must be learned without touching the linter.
    assert "ink-inverse" in colors


def test_missing_theme_block_is_an_error_not_an_empty_allowlist(tmp_path):
    css = tmp_path / "globals.css"
    css.write_text("body { color: red; }")
    with pytest.raises(SystemExit) as e:
        lint.parse_theme_tokens(css)
    assert e.value.code == 2


# ──────────────────────── border width vs border color ────────────────────


def test_bare_border_is_flagged(linter):
    lo, _, _ = linter
    assert "border-no-color" in rules(lo, "rounded-md border bg-card p-6")


def test_border_with_token_color_is_clean(linter):
    lo, _, _ = linter
    assert rules(lo, "rounded-md border border-rule bg-card p-6 shadow-sm") == {"primitive-reimpl"}


@pytest.mark.parametrize(
    "classes",
    [
        "border-t border-rule",
        "border-l-2 border-l-accent/50",  # side-prefixed color + width
        "border border-accent/30",  # opacity modifier
        "border border-transparent",  # keyword color
        "border-b border-rule-2",  # token whose name ENDS in a digit
    ],
)
def test_legitimate_border_forms_are_not_flagged(linter, classes):
    lo, _, _ = linter
    assert "border-no-color" not in rules(lo, classes)


def test_border_l_2_is_a_width_not_a_color(linter):
    """`border-l-2` (width) vs `border-rule-2` (color) can only be told apart
    by consulting the parsed token list — this is the disambiguation."""
    lo, _, _ = linter
    assert "border-no-color" in rules(lo, "border-l-2")
    assert "border-no-color" not in rules(lo, "border-rule-2")


def test_unresolvable_dynamic_composition_is_skipped(linter):
    """The components/ui.tsx shape: base template has `border`, the variant
    object supplies `border-rule-2`. Flagging this is the classic false
    positive that gets linters switched off."""
    lo, _, _ = linter
    assert "border-no-color" in rules(lo, "rounded-sm border px-2", allow_dynamic=False)
    assert "border-no-color" not in rules(lo, "rounded-sm border px-2", allow_dynamic=True)


# ──────────────────────────── raw palette / hex ────────────────────────────


def test_raw_tailwind_palette_is_flagged(linter):
    lo, _, _ = linter
    assert "raw-palette" in rules(lo, "border-red-300 bg-red-50 text-red-800")


def test_token_utilities_are_not_mistaken_for_raw_palette(linter):
    lo, _, _ = linter
    assert "raw-palette" not in rules(lo, "bg-paper text-ink-3 border-rule decoration-accent")


def test_hex_matching_a_token_is_a_duplicate_not_a_missing_token(linter):
    lo, _, _ = linter
    got = dict(lo.check("accent-[#8b3a2c]", rel="x.tsx", allow_dynamic_border=False))
    assert "hex-duplicate" in got
    assert "--color-accent" in got["hex-duplicate"]


def test_hex_matching_nothing_reports_an_incomplete_token_set(linter):
    lo, _, _ = linter
    assert "hex-missing-token" in rules(lo, "bg-[#123456]/55")


def test_shorthand_hex_is_normalized_before_comparison(linter):
    assert lint._norm_hex("#ABC") == "#aabbcc"


# ─────────────────────────── primitive reimplementation ────────────────────


def test_surface_exact_recipe_is_flagged(linter):
    lo, _, _ = linter
    assert "primitive-reimpl" in rules(lo, "rounded-md border border-rule bg-card p-3 shadow-sm")


def test_deliberate_divergence_is_not_flagged(linter):
    """The citation modal: rounded-lg + bespoke elevation. A considered choice,
    not drift."""
    lo, _, _ = linter
    assert "primitive-reimpl" not in rules(
        lo, "rounded-lg border border-rule-2 bg-card shadow-[0_24px_70px_-20px_rgba(26,22,15,0.5)]"
    )


def test_the_primitive_source_itself_is_exempt(linter):
    lo, _, _ = linter
    recipe = "rounded-md border border-rule bg-card shadow-sm"
    assert "primitive-reimpl" in rules(lo, recipe)
    assert "primitive-reimpl" not in rules(lo, recipe, rel=lint.PRIMITIVE_SOURCE)


# ────────────────────────────── class extraction ──────────────────────────


def test_plain_literal_classname():
    cands, dyn = lint.candidates_for('"rounded border"')
    assert cands == ["rounded border"] and not dyn


def test_template_base_and_variant_are_unioned_per_branch():
    cands, dyn = lint.candidates_for('{`base border ${on ? "border-accent" : "border-rule"}`}')
    assert sorted(cands) == ["base border border-accent", "base border border-rule"]
    assert dyn is False  # both branches are literals -> fully resolved


def test_identifier_interpolation_marks_the_blob_dynamic():
    _, dyn = lint.candidates_for("{`base border ${tones[tone]}`}")
    assert dyn is True


def test_ternary_branches_stay_separate_so_one_cannot_mask_the_other():
    """If both branches merged, branch A's missing border color would be hidden
    by branch B's `border-rule`."""
    cands, _ = lint.candidates_for('{isUser ? "rounded-md border" : "rounded-md border border-rule"}')
    assert "rounded-md border" in cands
    assert "rounded-md border border-rule" in cands


def test_bare_rounded_flagged_but_explicit_step_is_clean(linter):
    lo, _, _ = linter
    assert "bare-rounded" in rules(lo, "rounded border-rule border")
    assert "bare-rounded" not in rules(lo, "rounded-md border border-rule")


def test_variant_prefixes_are_stripped_before_matching(linter):
    lo, _, _ = linter
    assert "bare-rounded" in rules(lo, "md:rounded")
    assert "border-no-color" in rules(lo, "hover:border")
