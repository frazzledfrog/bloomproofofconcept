from datetime import date

import pytest

from bbg_mcp import changes

OBS = [
    (date(2025, 12, 30), 100.0),
    (date(2025, 12, 31), 110.0),  # year-end close
    (date(2026, 1, 2), 112.0),
    (date(2026, 3, 13), 120.0),  # Friday
    (date(2026, 3, 20), 121.0),  # Friday
    (date(2026, 3, 23), 132.0),  # Monday = latest
]


def test_anchor_dates_clamp_month_end():
    assert changes.anchor_date("1M", date(2026, 3, 31)) == date(2026, 2, 28)
    assert changes.anchor_date("YTD", date(2026, 3, 23)) == date(2025, 12, 31)
    assert changes.anchor_date("1Y", date(2026, 2, 28)) == date(2025, 2, 28)
    with pytest.raises(ValueError):
        changes.anchor_date("5Y", date(2026, 1, 1))


def test_value_on_or_before_uses_prior_close_over_weekends():
    assert changes.value_on_or_before(OBS, date(2026, 3, 22)) == (date(2026, 3, 20), 121.0)
    assert changes.value_on_or_before(OBS, date(2025, 1, 1)) is None


def test_period_changes():
    pc = changes.period_changes(OBS, ["1D", "1W", "YTD", "1Y"])
    assert pc["end"] == 132.0
    base, val, chg, pct = pc["periods"]["1D"][0], pc["periods"]["1D"][1], pc["periods"]["1D"][2], pc["periods"]["1D"][3]
    assert (base, val, chg) == (date(2026, 3, 20), 121.0, 11.0)
    assert pc["periods"]["1W"][:2] == (date(2026, 3, 13), 120.0)  # 1W anchor 03-16 -> prior close 03-13
    assert pc["periods"]["YTD"][1] == 110.0 and pc["periods"]["YTD"][3] == pytest.approx(20.0)
    assert pc["periods"]["1Y"] is None  # series does not reach back a year


def test_change_zero_base():
    assert changes.change(0.0, 1.0) == (1.0, None)
