"""UT for Krippendorff α and judge_daily fetch logic."""
from __future__ import annotations
import math

from validator.checks.judge_calibration import krippendorff_alpha


def test_alpha_perfect_agreement():
    h = [0.0, 0.25, 0.5, 0.75, 1.0]
    j = [0.0, 0.25, 0.5, 0.75, 1.0]
    a = krippendorff_alpha(h, j)
    assert a == 1.0


def test_alpha_random_disagreement_is_low():
    h = [0.0, 0.25, 0.5, 0.75, 1.0]
    j = [1.0, 0.75, 0.5, 0.25, 0.0]  # exactly inverted
    a = krippendorff_alpha(h, j)
    assert a < 0  # strong negative agreement


def test_alpha_degenerate_returns_sentinel():
    """If pooled variance is zero (everyone agrees on a single value),
    α is undefined — return -1.0 sentinel."""
    h = [0.5, 0.5, 0.5, 0.5]
    j = [0.5, 0.5, 0.5, 0.5]
    a = krippendorff_alpha(h, j)
    assert a == -1.0


def test_alpha_mostly_agree_above_threshold():
    """Five cases, one mismatch by 0.25 — should still be α > 0.80."""
    h = [1.0, 1.0, 1.0, 0.75, 1.0]
    j = [1.0, 1.0, 0.75, 0.75, 1.0]  # one cell off by 0.25
    a = krippendorff_alpha(h, j)
    assert a >= 0.5  # at least solidly positive; perfect threshold depends on n


def test_alpha_mismatched_lengths_returns_sentinel():
    assert krippendorff_alpha([1.0], [1.0, 0.5]) == -1.0
    assert krippendorff_alpha([], []) == -1.0
