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
    parser.addoption("--run-e2e", action="store_true", default=False,
                     help="run browser end-to-end tests (builds the frontend; no provider calls)")


def pytest_collection_modifyitems(config, items):
    for marker, option, reason in (("live", "--run-live", "live provider test; use --run-live"),
                                   ("e2e", "--run-e2e", "browser end-to-end test; use --run-e2e")):
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


def fixture_records(name: str) -> list[dict]:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))["records"]


TEST_CHAIN = (ChainEntry("gemini", "test-model"),)


@pytest.fixture
def settings(tmp_path) -> Settings:
    # A configured chain is required at startup (DEC-013); tests inject fake adapters.
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
