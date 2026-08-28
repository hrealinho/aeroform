"""Datetime boundary helpers.

The database stores timezone-aware timestamps, but sources are inconsistent: FIT
records can be naive, GPX carries a local offset, Strava sends UTC, and SQLite
silently drops tzinfo on write. Every value that crosses a boundary (parser ->
model, model -> query, model -> fingerprint) goes through here so comparisons and
hashes never depend on which representation a source happened to use.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone


def utcnow() -> datetime:
    """Timezone-aware "now". Use instead of the deprecated datetime.utcnow."""
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    """Attach UTC to a naive datetime. Naive values are treated as UTC because that
    is what every writer in this codebase produces before SQLite strips the offset."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def to_utc(value: datetime) -> datetime:
    """Normalize any datetime to UTC so equal instants compare and hash equal."""
    return ensure_aware(value).astimezone(timezone.utc)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def day_end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=timezone.utc)


def as_naive_utc(value: datetime) -> datetime:
    """UTC wall time with tzinfo removed, for JSON keys and stable string sorting."""
    return to_utc(value).replace(tzinfo=None)
