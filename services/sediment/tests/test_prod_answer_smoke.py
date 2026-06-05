from __future__ import annotations

import base64
import json

import pytest

from validator.scripts import prod_answer_smoke


def _unsigned_jwt(claims: dict) -> str:
    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(claims)}."


def test_validate_human_token_rejects_service_jwt():
    token = _unsigned_jwt({"sub": "service:github.nightly_recall", "role": "service"})

    with pytest.raises(RuntimeError, match="human member JWT"):
        prod_answer_smoke._validate_human_token(token)


def test_validate_human_token_accepts_member_jwt():
    token = _unsigned_jwt({"sub": "00000000-0000-0000-0000-000000000001", "role": "admin"})

    prod_answer_smoke._validate_human_token(token)
