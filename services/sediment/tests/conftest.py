import os
import pytest

# These tests need a running Postgres + seeded data.
# Skip if SKIP_DB=1 is set (used in CI when DB isn't available).
pytestmark = pytest.mark.skipif(os.environ.get("SKIP_DB") == "1", reason="DB not available")
