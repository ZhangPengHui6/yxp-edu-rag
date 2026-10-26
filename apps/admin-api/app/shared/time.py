"""UTC time helpers. Storage is UTC; the admin UI uses minute precision."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

DISPLAY_FORMAT = "%Y-%m-%d %H:%M"
DATE_FORMAT = "%Y-%m-%d"


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def fmt_dt(value: datetime | None, fallback: str = "-") -> str:
    """Format for the existing admin UI (``2026-10-24 10:24``).

    The 总纲 prescribes ISO 8601. The frontend contract established before this
    service displays timestamps verbatim, so the admin API returns minute
    precision strings and also exposes ``*_iso`` fields only where needed.
    """
    dt = ensure_utc(value)
    if dt is None:
        return fallback
    return dt.strftime(DISPLAY_FORMAT)


def fmt_date(value: datetime | date | None, fallback: str = "-") -> str:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        dt = ensure_utc(value)
        assert dt is not None
        return dt.strftime(DATE_FORMAT)
    return value.strftime(DATE_FORMAT)


def parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO 8601 or ``YYYY-MM-DD[ HH:MM]``; return UTC."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in (DATE_FORMAT, DISPLAY_FORMAT):
            try:
                parsed = datetime.strptime(value.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def days_ago(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


def day_label(value: datetime) -> str:
    return ensure_utc(value).strftime("%m-%d")  # type: ignore[union-attr]
