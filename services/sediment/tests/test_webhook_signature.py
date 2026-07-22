"""Unit tests for the vault-ingester webhook signature gate (sediment#81).

The webhook signature is the only thing standing between the ingester and a
forged payload writing attacker-controlled markdown into the vault. These
tests lock the fail-CLOSED contract: verification must reject when the secret
is missing, when the signature header is absent/malformed, and when the
signature does not match — and accept only a correct HMAC-SHA256.

No DB / network is touched: `_verify_github_sig` is a pure function over
(raw_body, header) + the GITHUB_WEBHOOK_SECRET env var.
"""
from __future__ import annotations

import hashlib
import hmac

from applications.vault_ingester.main import _verify_github_sig

# These tests never need a DB — override the DB-skip marker from conftest.
pytestmark = []


def _sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


RAW = b'{"ref":"main","files":[]}'


def test_missing_secret_fails_closed(monkeypatch):
    # THE fix for sediment#81: no secret configured -> reject, even with a
    # syntactically plausible header. Previously this returned True (fail-open).
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    assert _verify_github_sig(RAW, _sign("anything", RAW)) is False
    # ...and reject when there is no header either.
    assert _verify_github_sig(RAW, None) is False


def test_empty_secret_fails_closed(monkeypatch):
    # An empty-string secret is as good as unset — still reject.
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    assert _verify_github_sig(RAW, _sign("", RAW)) is False


def test_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    assert _verify_github_sig(RAW, None) is False


def test_malformed_header_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    # Missing the "sha256=" prefix / wrong algo prefix.
    assert _verify_github_sig(RAW, "deadbeef") is False
    assert _verify_github_sig(RAW, "sha1=deadbeef") is False


def test_wrong_signature_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    # Correctly formatted but signed with the wrong secret.
    assert _verify_github_sig(RAW, _sign("wrongsecret", RAW)) is False


def test_tampered_body_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    sig = _sign("topsecret", RAW)
    # Same signature, mutated body -> reject.
    assert _verify_github_sig(RAW + b"tampered", sig) is False


def test_valid_signature_accepted(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    assert _verify_github_sig(RAW, _sign("topsecret", RAW)) is True
