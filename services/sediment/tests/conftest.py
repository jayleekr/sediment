import pytest

# ---------------------------------------------------------------------------
# DO NOT put `pytestmark` here. It does nothing (sediment#154).
#
# pytest collects `pytestmark` from test modules and classes only; a module-level
# assignment in conftest.py is silently ignored. This file carried one for
# months, so `SKIP_DB=1` never skipped tests/test_rls.py or the DB-touching test
# in tests/test_search_bm25.py — running the suite without Postgres produced 38
# failures every time.
#
# The cost was not the noise. A permanently red baseline hides real regressions:
# two of them (sediment#155, sediment#156) sat undetected behind it, and one of
# those was the only regression guard for Korean-particle stripping.
#
# Each module that needs a live DB declares its own skip marker — the existing
# convention in ~15 files here:
#
#     pytestmark = pytest.mark.skipif(
#         os.environ.get("SKIP_DB") == "1", reason="DB not available")
#
# For a module where only some tests need the DB, mark those tests individually
# so the pure-logic ones keep running.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_dev_mode_for_tests(monkeypatch):
    """Many tests call /api/v1/auth/dev-token to mint a JWT. In production
    that endpoint is gated by SEDIMENT_DEV_MODE=1 (sediment#dev-token-gate).
    Tests run with the gate open by default; specific tests that need the
    gate closed override via monkeypatch.delenv.
    """
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
