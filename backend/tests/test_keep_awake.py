"""Keep-awake during a daily window (DEC-026).

The deployed free-tier service pings its own public url on an interval so it does not sleep during
demo hours. These tests cover the parts that decide WHETHER to ping: the window, its timezone and
its wrap over midnight, plus the configuration guards. The ping itself is one httpx GET.

The important negative: nothing is enabled unless KEEP_AWAKE_URL is set, so no test, no CI run and
no local server ever pings anything.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.config import ConfigError, Settings
from app.services import keep_awake

IST = ZoneInfo("Asia/Kolkata")


def settings(**over) -> Settings:
    base = dict(database_path="x.db", uploads_dir="up", frontend_origin="http://localhost:3000",
                demo_reset_token="t")
    return Settings(**{**base, **over})


def at(hour: int, minute: int = 0, tz: ZoneInfo = IST) -> dt.datetime:
    return dt.datetime(2026, 11, 11, hour, minute, tzinfo=tz)


# ------------------------------------------------------------------ disabled unless configured

def test_keep_awake_is_off_unless_a_url_is_set():
    assert keep_awake.from_settings(settings()) is None
    assert keep_awake.from_settings(settings(keep_awake_url="   ")) is None


def test_the_default_settings_carry_no_url():
    """The default must be off: a default url would make every local run ping the deployed host."""
    assert settings().keep_awake_url is None


# ------------------------------------------------------------------ the window

@pytest.mark.parametrize("hour,inside", [(6, False), (7, True), (12, True), (22, True), (23, False), (2, False)])
def test_the_window_includes_its_start_and_excludes_its_end(hour, inside):
    cfg = keep_awake.from_settings(settings(keep_awake_url="https://x.example"))
    assert cfg.in_window(at(hour)) is inside


def test_a_window_may_wrap_midnight():
    cfg = keep_awake.from_settings(settings(keep_awake_url="https://x.example",
                                            keep_awake_window="22:00-06:00"))
    assert cfg.in_window(at(23)) and cfg.in_window(at(1)) and cfg.in_window(at(22))
    assert not cfg.in_window(at(6)) and not cfg.in_window(at(12))


def test_the_window_is_read_in_its_own_timezone_not_the_servers():
    """Render runs in UTC; 09:00 IST is 03:30 UTC and must count as inside a 07:00-23:00 window."""
    cfg = keep_awake.from_settings(settings(keep_awake_url="https://x.example"))
    nine_ist_as_utc = dt.datetime(2026, 11, 11, 3, 30, tzinfo=dt.timezone.utc)
    assert cfg.in_window(nine_ist_as_utc)
    two_ist_as_utc = dt.datetime(2026, 11, 11, 20, 30, tzinfo=dt.timezone.utc)   # 02:00 IST
    assert not cfg.in_window(two_ist_as_utc)


def test_the_window_covers_sixteen_hours_by_default():
    """480 h a month against Render's 750 free instance-hours: the point of having a window."""
    cfg = keep_awake.from_settings(settings(keep_awake_url="https://x.example"))
    hours = sum(1 for h in range(24) if cfg.in_window(at(h)))
    assert hours == 16


# ------------------------------------------------------------------ configuration guards

def test_the_url_must_be_absolute_so_the_ping_leaves_the_container():
    """A request to localhost never reaches Render's edge and resets nothing."""
    with pytest.raises(ConfigError, match="absolute"):
        keep_awake.from_settings(settings(keep_awake_url="clearmatch-ap-backend.onrender.com"))


def test_the_health_path_is_appended_once():
    cfg = keep_awake.from_settings(settings(keep_awake_url="https://x.example/"))
    assert cfg.url == "https://x.example/api/health"


@pytest.mark.parametrize("interval", [0, -5, 900, 3600])
def test_an_interval_that_lets_the_host_sleep_is_refused(interval):
    with pytest.raises(ConfigError, match="INTERVAL"):
        keep_awake.from_settings(settings(keep_awake_url="https://x.example",
                                          keep_awake_interval_seconds=interval))


@pytest.mark.parametrize("spec", ["07:00", "", "07:00-", "9am-5pm", "07:00-07:00"])
def test_a_malformed_window_is_refused_at_startup(spec):
    with pytest.raises(ConfigError, match="KEEP_AWAKE_WINDOW"):
        keep_awake.from_settings(settings(keep_awake_url="https://x.example", keep_awake_window=spec))


def test_an_unknown_timezone_is_refused_at_startup():
    with pytest.raises(ConfigError, match="KEEP_AWAKE_TZ"):
        keep_awake.from_settings(settings(keep_awake_url="https://x.example",
                                          keep_awake_tz="Mars/Olympus_Mons"))


# ------------------------------------------------------------------ the ping

@pytest.mark.anyio
async def test_a_failed_ping_is_swallowed_so_the_loop_survives():
    cfg = keep_awake.from_settings(settings(keep_awake_url="http://127.0.0.1:1"))   # nothing listens
    assert await keep_awake.ping_once(cfg) is False


# ------------------------------------------------------------------ wired into the app

def test_the_running_app_pings_itself_and_stops_at_shutdown(tmp_path):
    """End to end through create_app: the task starts with the app, pings on its interval, and is
    cancelled on shutdown. A stand-in HTTP server plays the part of the public url."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import TEST_CHAIN

    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                     # noqa: N802 - http.server's name
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):         # keep pytest output clean
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = create_app(settings(
            database_path=tmp_path / "x.db", uploads_dir=tmp_path / "up", gemini_api_key="unused",
            text_chain=TEST_CHAIN, vision_chain=TEST_CHAIN,
            keep_awake_url=f"http://127.0.0.1:{server.server_address[1]}",
            keep_awake_window="00:00-23:59", keep_awake_tz="UTC", keep_awake_interval_seconds=1))
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            time.sleep(2.5)
        pinged = len(hits)
        assert pinged >= 1, "the keep-awake task never pinged"
        assert set(hits) == {"/api/health"}
        time.sleep(1.5)
        assert len(hits) == pinged, "the task kept pinging after the app shut down"
    finally:
        server.shutdown()


def test_meta_reports_whether_this_instance_holds_itself_awake(tmp_path):
    """The only way to tell from outside that a deploy picked the settings up."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import TEST_CHAIN

    common = dict(database_path=tmp_path / "x.db", uploads_dir=tmp_path / "up",
                  gemini_api_key="unused", text_chain=TEST_CHAIN, vision_chain=TEST_CHAIN)

    off = TestClient(create_app(settings(**common))).get("/api/meta").json()["keep_awake"]
    assert off == {"enabled": False}

    on = TestClient(create_app(settings(
        **common, keep_awake_url="https://x.example"))).get("/api/meta").json()["keep_awake"]
    assert on == {"enabled": True, "window": "07:00-23:00",
                  "timezone": "Asia/Kolkata", "interval_seconds": 600.0}
    # The url is a public hostname, but it is still not part of the payload.
    assert "x.example" not in str(on)
