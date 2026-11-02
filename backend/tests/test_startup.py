"""The backend refuses to start without DEMO_RESET_TOKEN (FR-116, DEC-002)."""
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import ConfigError, load_settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_load_settings_requires_token(monkeypatch):
    monkeypatch.delenv("DEMO_RESET_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="DEMO_RESET_TOKEN"):
        load_settings()


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_token_rejected(monkeypatch, value):
    monkeypatch.setenv("DEMO_RESET_TOKEN", value)
    with pytest.raises(ConfigError):
        load_settings()


def test_cli_settings_do_not_need_token(monkeypatch):
    monkeypatch.delenv("DEMO_RESET_TOKEN", raising=False)
    assert load_settings(require_reset_token=False).demo_reset_token is None


def test_create_app_refuses_without_token(settings):
    with pytest.raises(ConfigError):
        create_app(dataclasses.replace(settings, demo_reset_token=None))
    assert not settings.database_path.exists()   # refused before touching the database


def test_token_not_in_settings_repr(settings):
    assert settings.demo_reset_token not in repr(settings)


def _server_env(tmp_path, token):
    env = {k: v for k, v in os.environ.items() if k != "DEMO_RESET_TOKEN"}
    env["DATABASE_PATH"] = str(tmp_path / "srv.db")
    env["UPLOADS_DIR"] = str(tmp_path / "uploads")
    if token is not None:
        env["DEMO_RESET_TOKEN"] = token
    return env


def test_uvicorn_exits_without_token(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
        cwd=BACKEND_DIR, env=_server_env(tmp_path, None),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "DEMO_RESET_TOKEN" in proc.stderr
    assert not (tmp_path / "srv.db").exists()


def test_app_builds_with_token(tmp_path):
    code = "import app.main as m; print(type(m.app).__name__)"
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_DIR, env=_server_env(tmp_path, "abc"),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "FastAPI"
