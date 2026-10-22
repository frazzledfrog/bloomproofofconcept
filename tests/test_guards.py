from datetime import date

import pytest

from bbg_mcp.guards import GuardError, HitBudget, TTLCache, normalize_fields, normalize_list, resolve_window

TODAY = date(2026, 10, 22)


def test_normalize_list_dedupes_and_collapses_whitespace():
    assert normalize_list(["AAPL  US Equity", "AAPL US Equity", " SPX Index "], "security", 5) == [
        "AAPL US Equity",
        "SPX Index",
    ]
    assert normalize_list("SPX Index", "security", 5) == ["SPX Index"]


def test_normalize_list_limits():
    with pytest.raises(GuardError, match="limit is 2"):
        normalize_list(["a", "b", "c"], "security", 2)
    with pytest.raises(GuardError):
        normalize_list([], "security", 2)


def test_fields_uppercased():
    assert normalize_fields(["px_last"], 5) == ["PX_LAST"]


def test_window_defaults_to_trailing_year():
    assert resolve_window(None, None, 366, 365, today=TODAY) == (date(2025, 10, 22), TODAY)


def test_window_clamps_future_end_and_rejects_long_spans():
    assert resolve_window("2026-10-01", "2027-01-01", 366, 365, today=TODAY) == (date(2026, 10, 1), TODAY)
    with pytest.raises(GuardError, match="limit is 366"):
        resolve_window("2024-01-01", "2026-10-01", 366, 365, today=TODAY)
    with pytest.raises(GuardError, match="after"):
        resolve_window("2026-10-10", "2026-10-01", 366, 365, today=TODAY)
    with pytest.raises(GuardError, match="YYYY-MM-DD"):
        resolve_window("last tuesday", None, 366, 365, today=TODAY)


def test_hit_budget_persists_and_refuses(tmp_path):
    db = tmp_path / "u.sqlite3"
    b = HitBudget(db, 10)
    b.charge(6)
    with pytest.raises(GuardError, match="budget"):
        b.charge(5)
    assert HitBudget(db, 10).used_today() == 6


def test_ttl_cache_expires():
    now = [0.0]
    c = TTLCache(clock=lambda: now[0])
    c.set("k", "v", ttl=10)
    assert c.get("k") == "v"
    now[0] = 10
    assert c.get("k") is None
