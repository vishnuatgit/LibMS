import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "libms-import.db"))

import pytest  # noqa: E402

from app import app, init_db  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app.config["DATABASE"] = str(tmp_path / "test.db")
    app.config["TESTING"] = True
    app.config["RATELIMIT_ENABLED"] = False
    with app.app_context():
        init_db()
    with app.test_client() as client:
        yield client
