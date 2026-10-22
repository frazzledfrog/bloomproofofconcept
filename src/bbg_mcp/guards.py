"""Request validation, hit budgeting and caching.

DAPI enforces per-user daily/monthly data limits; exceeding them locks the
user's API access. An LLM calling tools in a loop can get there quickly, so
every request goes through these guards before it reaches the Terminal.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


class GuardError(ValueError):
    """A request was refused before reaching Bloomberg. Message is model-facing."""


def normalize_list(values: list[str] | str, what: str, limit: int) -> list[str]:
    if isinstance(values, str):
        values = [values]
    cleaned = []
    for v in values:
        v = " ".join(str(v).split())
        if v and v not in cleaned:
            cleaned.append(v)
    if not cleaned:
        raise GuardError(f"At least one {what} is required.")
    if len(cleaned) > limit:
        raise GuardError(
            f"Too many {what}s ({len(cleaned)}); the limit is {limit} per call. "
            f"Split the request into smaller batches."
        )
    return cleaned


def normalize_fields(fields: list[str] | str, limit: int) -> list[str]:
    return [f.upper() for f in normalize_list(fields, "field", limit)]


def parse_date(value: str | date | None, name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise GuardError(f"{name} must be YYYY-MM-DD, got {value!r}.")


def resolve_window(
    start: str | date | None,
    end: str | date | None,
    max_days: int,
    default_days: int,
    today: date | None = None,
) -> tuple[date, date]:
    """Fill in a default trailing window and refuse windows over max_days."""
    today = today or date.today()
    end_d = parse_date(end, "end_date") or today
    start_d = parse_date(start, "start_date") or end_d - timedelta(days=default_days)
    if end_d > today:
        end_d = today
    if start_d > end_d:
        raise GuardError(f"start_date {start_d} is after end_date {end_d}.")
    span = (end_d - start_d).days
    if span > max_days:
        raise GuardError(
            f"Requested window is {span} days; the limit is {max_days}. "
            f"Narrow start_date/end_date."
        )
    return start_d, end_d


class HitBudget:
    """Approximate daily hit counter persisted in SQLite.

    One "hit" is one security x field per request. This is a conservative
    proxy for Bloomberg's own accounting, which is not published exactly.
    """

    def __init__(self, path: Path, daily_limit: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._limit = daily_limit
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS hits (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")
        self._db.commit()

    def used_today(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT count FROM hits WHERE day = ?", (date.today().isoformat(),)).fetchone()
        return row[0] if row else 0

    @property
    def limit(self) -> int:
        return self._limit

    def charge(self, hits: int) -> None:
        """Reserve hits, or raise GuardError if that would exceed today's limit."""
        day = date.today().isoformat()
        with self._lock:
            row = self._db.execute("SELECT count FROM hits WHERE day = ?", (day,)).fetchone()
            used = row[0] if row else 0
            if used + hits > self._limit:
                raise GuardError(
                    f"Daily Bloomberg hit budget would be exceeded ({used} used + {hits} requested "
                    f"> {self._limit}). Reuse earlier results or ask the user to raise daily_hit_budget."
                )
            self._db.execute(
                "INSERT INTO hits (day, count) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET count = count + excluded.count",
                (day, hits),
            )
            self._db.commit()


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(*parts: Any) -> str:
        return json.dumps(parts, sort_keys=True, default=str)

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if self._clock() >= expires:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (self._clock() + ttl, value)
