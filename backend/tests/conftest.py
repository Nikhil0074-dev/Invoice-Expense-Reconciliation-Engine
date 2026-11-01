import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("DB_PATH", os.path.join(d, "t.db"))
    monkeypatch.setenv("STORAGE_DIR", os.path.join(d, "files"))
    from fastapi.testclient import TestClient
    from backend.app import db
    from backend.app.main import app
    db.init()
    return TestClient(app)
