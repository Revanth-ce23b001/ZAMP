import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.connection import connect
from app.db.migrate import migrate
from app.db.seed import SEED_DIR, seed
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_TOKEN = "test-reset-token"


def fixture_records(name: str) -> list[dict]:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))["records"]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "test.db",
        uploads_dir=tmp_path / "uploads",
        frontend_origin="http://localhost:3000",
        demo_reset_token=TEST_TOKEN,
    )


@pytest.fixture
def empty_db(tmp_path):
    conn = connect(tmp_path / "empty.db")
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(tmp_path):
    conn = connect(tmp_path / "seeded.db")
    migrate(conn)
    seed(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
