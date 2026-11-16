"""Today's date for the decisions (Spanish calendar days), in one place so tests can fix it."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def hoy() -> date:
    try:
        return datetime.now(ZoneInfo("Europe/Madrid")).date()
    except ZoneInfoNotFoundError:  # pragma: no cover - only without a tz database
        return datetime.now(UTC).date()


__all__ = ["hoy"]
