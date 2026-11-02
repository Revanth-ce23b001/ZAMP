"""Timestamps are ISO 8601 with an explicit offset (PRD §8)."""
from datetime import datetime, timedelta, timezone

# India Standard Time, fixed offset (no DST). Avoids a tzdata dependency on Windows.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")
