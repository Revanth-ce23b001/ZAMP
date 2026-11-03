import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ChainEntry, Settings
from app.db.connection import connect
from app.db.migrate import migrate
from app.db.seed import SEED_DIR, seed
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_TOKEN = "test-reset-token"


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run tests marked 'live' that call the real LLM provider (costs money)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="live provider test; use --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def fixture_records(name: str) -> list[dict]:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))["records"]


TEST_CHAIN = (ChainEntry("gemini", "test-model"),)


@pytest.fixture
def settings(tmp_path) -> Settings:
    # A configured chain is required at startup (DEC-015); tests inject fake adapters.
    # cache_snapshot=None keeps the shipped cache out of tests that count provider calls.
    return Settings(
        database_path=tmp_path / "test.db",
        uploads_dir=tmp_path / "uploads",
        frontend_origin="http://localhost:3000",
        demo_reset_token=TEST_TOKEN,
        gemini_api_key="test-gemini-key",
        text_chain=TEST_CHAIN,
        vision_chain=TEST_CHAIN,
        cache_snapshot=None,
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
