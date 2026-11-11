"""Holds the free-tier instance awake during a daily window (DEC-026).

Render's free plan sleeps a service after about 15 minutes without inbound traffic, and waking it
again takes 30-45 s. That wake is the whole of the demo's first-load delay: the backend's own
start-up is 0.34 s (migrate, seed, cache snapshot).

Scheduled GitHub Actions were meant to prevent the sleep, but GitHub throttles high-frequency
schedules. The `*/10` keep-warm workflow actually ran every 4 to 8 hours (measured over
2026-11-09..11), and its own run durations - 39 s, 50 s, 40 s against 6 s when warm - show the
service was asleep nearly every time it fired. So the demo paid the cold start almost always.

While it is awake, the service can hold itself awake. A request to its own PUBLIC url leaves the
container, reaches Render's edge and comes back as ordinary inbound traffic, which resets the idle
timer. Pinging 127.0.0.1 would not: Render counts traffic at the edge, not inside the container.

Awake time costs instance hours (750 free a month), so this runs only inside a daily window. About
16 hours a day is roughly 480 hours a month - comfortably inside the quota, with the service
allowed to sleep overnight. This cannot wake a service that is already asleep, because nothing in
the container runs then; the scheduled workflow opens the window, and this keeps it open.

Nothing happens unless KEEP_AWAKE_URL is set, so local runs, CI and the tests are unaffected.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.config import ConfigError

log = logging.getLogger("clearmatch.keep_awake")

PING_TIMEOUT_SECONDS = 30.0


def parse_window(spec: str) -> tuple[dt.time, dt.time]:
    """'07:00-23:00' -> (07:00, 23:00). A window may wrap midnight ('22:00-06:00')."""
    start_text, sep, end_text = spec.partition("-")
    if not sep:
        raise ConfigError(f"KEEP_AWAKE_WINDOW {spec!r} must look like 07:00-23:00")
    try:
        start = dt.time.fromisoformat(start_text.strip())
        end = dt.time.fromisoformat(end_text.strip())
    except ValueError as exc:
        raise ConfigError(f"KEEP_AWAKE_WINDOW {spec!r} must look like 07:00-23:00") from exc
    if start == end:
        raise ConfigError(f"KEEP_AWAKE_WINDOW {spec!r} is empty: start and end are the same time")
    return start, end


def parse_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"KEEP_AWAKE_TZ {name!r} is not a known timezone") from exc


HEALTH_PATH = "/api/health"
# Render sleeps after about 15 minutes idle, so a longer interval would let it sleep anyway.
MAX_INTERVAL_SECONDS = 840.0


@dataclass(frozen=True)
class KeepAwake:
    """When to ping, and what. Built from settings; see load_settings()."""
    url: str
    start: dt.time
    end: dt.time
    tz: ZoneInfo
    interval_seconds: float

    def in_window(self, now: dt.datetime) -> bool:
        """Start inclusive, end exclusive, in this window's own timezone."""
        local = now.astimezone(self.tz).time()
        if self.start <= self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end        # wraps midnight

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz)


def from_settings(settings) -> KeepAwake | None:
    """Build the config, or None when KEEP_AWAKE_URL is unset (the default: disabled).

    KEEP_AWAKE_URL is the service's own public base url, e.g.
    https://clearmatch-ap-backend.onrender.com. It must be the public one, not localhost: a
    request that never leaves the container does not reset Render's idle timer.
    """
    base = (settings.keep_awake_url or "").strip().rstrip("/")
    if not base:
        return None
    if not base.startswith(("http://", "https://")):
        raise ConfigError(f"KEEP_AWAKE_URL {base!r} must be an absolute http(s) url")
    interval = float(settings.keep_awake_interval_seconds)
    if not 0 < interval <= MAX_INTERVAL_SECONDS:
        raise ConfigError(
            f"KEEP_AWAKE_INTERVAL_SECONDS must be between 1 and {MAX_INTERVAL_SECONDS:.0f} "
            f"(the host sleeps after about 15 minutes idle), got {interval:g}")
    start, end = parse_window(settings.keep_awake_window)
    return KeepAwake(url=base + HEALTH_PATH, start=start, end=end,
                     tz=parse_timezone(settings.keep_awake_tz), interval_seconds=interval)


async def ping_once(cfg: KeepAwake) -> bool:
    """One ping. Returns whether it reached the service; never raises."""
    try:
        async with httpx.AsyncClient(timeout=PING_TIMEOUT_SECONDS) as client:
            res = await client.get(cfg.url)
        log.info("keep-awake ping %s -> %s", cfg.url, res.status_code)
        return res.status_code < 500
    except Exception as exc:                                   # noqa: BLE001 - never kill the loop
        log.warning("keep-awake ping %s failed: %s", cfg.url, exc)
        return False


async def run(cfg: KeepAwake) -> None:
    """Ping on every interval that falls inside the window. Cancelled at shutdown."""
    log.info("keep-awake on: %s every %.0fs between %s and %s %s",
             cfg.url, cfg.interval_seconds, cfg.start, cfg.end, cfg.tz.key)
    while True:
        if cfg.in_window(cfg.now()):
            await ping_once(cfg)
        await asyncio.sleep(cfg.interval_seconds)
