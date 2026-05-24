import base64
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from applications.sediment_platform.routers import issuer


def _auth(user: str, password: str) -> str:
    raw = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {raw}"


def _payload(token: str) -> dict:
    payload = token.split(".", 1)[0]
    pad = "=" * ((4 - len(payload) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(payload + pad).decode("utf-8"))


def test_rotate_can_include_session_control_scope(monkeypatch):
    monkeypatch.setenv("HPS_SIGNING_SECRET", "test-signing-secret-123")
    monkeypatch.setenv("INSTRUCTOR_JIWOONG_PASSPHRASE", "pw")

    app = FastAPI()
    app.include_router(issuer.router, prefix="/api/v1/issuer")
    client = TestClient(app)

    res = client.post(
        "/api/v1/issuer/jiwoong/rotate",
        headers={"Authorization": _auth("jiwoong", "pw")},
        json={"confirm": True, "can_start_session": True, "max_session_hours": 4},
    )

    assert res.status_code == 200
    body = res.json()
    assert all(scope["can_start_session"] is True for scope in body["scopes"])
    assert all(scope["max_session_hours"] == 4 for scope in body["scopes"])

    payload = _payload(body["token"])
    assert payload["role"] == "issuer"
    assert all(scope["can_start_session"] is True for scope in payload["scopes"])
    assert all(scope["max_session_hours"] == 4 for scope in payload["scopes"])


def test_rotate_rejects_invalid_session_duration_cap(monkeypatch):
    monkeypatch.setenv("HPS_SIGNING_SECRET", "test-signing-secret-123")
    monkeypatch.setenv("INSTRUCTOR_JIWOONG_PASSPHRASE", "pw")

    app = FastAPI()
    app.include_router(issuer.router, prefix="/api/v1/issuer")
    client = TestClient(app)

    res = client.post(
        "/api/v1/issuer/jiwoong/rotate",
        headers={"Authorization": _auth("jiwoong", "pw")},
        json={"confirm": True, "can_start_session": True, "max_session_hours": 25},
    )

    assert res.status_code == 400
    assert "max_session_hours" in res.json()["detail"]
