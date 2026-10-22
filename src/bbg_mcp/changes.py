"""Period-change arithmetic over a daily series (pure, no Bloomberg calls)."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

PERIODS = ("1D", "1W", "2W", "1M", "3M", "6M", "YTD", "1Y")
DEFAULT_PERIODS = ("1D", "1W", "1M", "3M", "YTD", "1Y")


def _minus_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def anchor_date(period: str, end: date) -> date | None:
    """Calendar date the period is measured from (None for 1D: previous observation)."""
    match period:
        case "1D":
            return None
        case "1W":
            return end - timedelta(days=7)
        case "2W":
            return end - timedelta(days=14)
        case "1M":
            return _minus_months(end, 1)
        case "3M":
            return _minus_months(end, 3)
        case "6M":
            return _minus_months(end, 6)
        case "YTD":
            return date(end.year - 1, 12, 31)
        case "1Y":
            return _minus_months(end, 12)
    raise ValueError(f"Unknown period {period!r}; use one of {PERIODS}.")


def observations(rows: list[dict[str, Any]], field: str) -> list[tuple[date, float]]:
    """(date, value) pairs with numeric values, oldest first."""
    obs = []
    for row in rows:
        v = row.get(field)
        if isinstance(v, (int, float)):
            obs.append((date.fromisoformat(str(row["date"])[:10]), float(v)))
    obs.sort()
    return obs


def value_on_or_before(obs: list[tuple[date, float]], d: date) -> tuple[date, float] | None:
    """Last observation dated on or before d (the closing level as of d)."""
    found = None
    for od, v in obs:
        if od > d:
            break
        found = (od, v)
    return found


def change(start: float, end: float) -> tuple[float, float | None]:
    """(absolute change, percent change); percent is None when start is 0."""
    return end - start, (end / start - 1) * 100 if start else None


def period_changes(obs: list[tuple[date, float]], periods: list[str]) -> dict[str, Any]:
    """Changes from each period's anchor to the latest observation."""
    if not obs:
        return {}
    end_d, end_v = obs[-1]
    out: dict[str, Any] = {"end_date": end_d, "end": end_v, "periods": {}}
    for p in periods:
        anchor = anchor_date(p, end_d)
        base = (obs[-2] if len(obs) > 1 else None) if anchor is None else value_on_or_before(obs, anchor)
        if base is None or base[0] >= end_d:
            out["periods"][p] = None
        else:
            out["periods"][p] = (base[0], base[1], *change(base[1], end_v))
    return out


def invert(obs: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """1/x of each observation, e.g. USDCAD -> CADUSD. Zeros are dropped."""
    return [(d, 1.0 / v) for d, v in obs if v]


def ratio(obs: list[tuple[date, float]], base: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """obs / base on each date both have a value (cross rates, relative performance)."""
    b = dict(base)
    return [(d, v / b[d]) for d, v in obs if b.get(d)]
