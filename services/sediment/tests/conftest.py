import os

import pytest

# These tests need a running Postgres + seeded data.
# Skip if SKIP_DB=1 is set (used in CI when DB isn't available).
pytestmark = pytest.mark.skipif(os.environ.get("SKIP_DB") == "1", reason="DB not available")


@pytest.fixture(autouse=True)
def _enable_dev_mode_for_tests(monkeypatch):
    """Many tests call /api/v1/auth/dev-token to mint a JWT. In production
    that endpoint is gated by SEDIMENT_DEV_MODE=1 (sediment#dev-token-gate).
    Tests run with the gate open by default; specific tests that need the
    gate closed override via monkeypatch.delenv.
    """
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
