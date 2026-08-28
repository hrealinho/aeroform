"""Test fixtures.

Database and storage settings must be set before anything imports app.main, which calls
create_all() against the engine built at import time.
"""
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="aeroform-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("STORAGE_PATH", f"{_TMP}/raw")
os.environ.setdefault("ASYNC_TASKS", "false")
os.environ.setdefault("APP_SECRET", "test-secret-not-for-production")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """A fresh database for one test, isolated from the module-level engine."""
    return tmp_path / "isolated.db"


@pytest.fixture()
def client():
    """FastAPI TestClient against a per-test database.

    Server exceptions are not re-raised so a test sees the status code a real caller
    would see - a 500 must fail an assertion, not surface as a pytest error.
    """
    from fastapi.testclient import TestClient

    from app.db.session import Base, engine
    from app.main import app

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def session():
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def athlete(client, session):
    from app.api.routes import demo_athlete

    return demo_athlete(session)
