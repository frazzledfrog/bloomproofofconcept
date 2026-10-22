import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bbg_mcp.server import build_server


def call(server, name, **args):
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error
    return result.content[0].text


@pytest.fixture
def server(svc):
    return build_server(svc)


def test_tools_registered(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {
        "bbg_status",
        "get_historical_data",
        "get_reference_data",
        "get_price_changes",
        "get_intraday_bars",
        "search_securities",
        "search_fields",
        "get_news_signals",
        "get_security_news_signals",
    }


def test_historical_default_year(server):
    text = call(server, "get_historical_data", securities=["SPX Index"], fields=["px_last"])
    assert text.startswith("Historical DAILY")
    assert "## SPX Index" in text
    assert "date,PX_LAST" in text
    rows = [l for l in text.splitlines() if l[:2] == "20"]
    assert 250 <= len(rows) <= 263


def test_historical_reports_bad_security(server):
    text = call(server, "get_historical_data", securities=["INVALID Equity"], fields=["PX_LAST"])
    assert "ERROR: Unknown/Invalid security" in text


def test_oversized_request_refused_with_guidance(server):
    with pytest.raises(ToolError, match="limit is 366"):
        asyncio.run(
            server.call_tool(
                "get_historical_data", {"securities": ["SPX Index"], "fields": ["PX_LAST"], "start_date": "2020-01-01"}
            )
        )


def test_repeat_request_served_from_cache(server, backend, svc):
    args = dict(securities=["AAPL US Equity", "MSFT US Equity"], fields=["PX_LAST", "NAME"])
    first = call(server, "get_reference_data", **args)
    used = svc.budget.used_today()
    second = call(server, "get_reference_data", **args)
    assert first == second
    assert len(backend.calls) == 1
    assert used == 4 and svc.budget.used_today() == 4


def test_reference_field_exception_surfaced(server):
    text = call(server, "get_reference_data", securities=["AAPL US Equity"], fields=["PX_LAST", "BAD_FIELD"])
    assert "FIELD ERROR: BAD_FIELD" in text


def test_search_tools(server):
    assert "AAPL US Equity" in call(server, "search_securities", query="apple")
    assert "PX_LAST" in call(server, "search_fields", query="last price")


def test_intraday(server):
    text = call(
        server, "get_intraday_bars", security="AAPL US Equity", interval_minutes=30,
        start_datetime="2026-10-19T00:00", end_datetime="2026-10-21T00:00",
    )
    assert "time,open,high,low,close,volume,numEvents" in text


def test_news_signals_ranked(server):
    text = call(server, "get_news_signals", topic="energy")
    lines = text.splitlines()
    assert "not headlines" in lines[1]
    heat = [float(l.split(",")[1]) for l in lines[3:] if l]
    assert heat == sorted(heat, reverse=True) and len(heat) == 5


def test_unknown_topic(server):
    with pytest.raises(ToolError, match="Unknown topic"):
        asyncio.run(server.call_tool("get_news_signals", {"topic": "gossip"}))


def test_status(server):
    assert "connected: True" in call(server, "bbg_status")


def test_price_changes_default_periods(server, svc):
    text = call(server, "get_price_changes", securities=["USDCAD Curncy", "SPX Index"])
    lines = text.splitlines()
    assert lines[1] == "security,date,last," + ",".join(
        f"{p}_{k}" for p in ("1D", "1W", "1M", "3M", "YTD", "1Y") for k in ("pct", "chg")
    )
    row = lines[2].split(",")
    assert row[0] == "USDCAD Curncy" and len(row) == 15 and all(row[3:])
    assert svc.budget.used_today() == 2  # one field x two securities, one request


def test_price_changes_custom_range(server):
    text = call(
        server, "get_price_changes", securities=["EURUSD Curncy"], start_date="2026-01-03", end_date="2026-06-30"
    )
    header, row = text.splitlines()[2:4]
    assert header == "security,start_date,start,end_date,end,ratio_end_over_start,chg,pct_change,high,low"
    cells = row.split(",")
    assert cells[1] == "2026-01-02"  # Saturday start -> prior close
    start, end, ratio, pct = float(cells[2]), float(cells[4]), float(cells[5]), float(cells[7])
    assert ratio == pytest.approx(end / start, rel=1e-5)
    assert pct == pytest.approx((end / start - 1) * 100, abs=1e-3)
    assert float(cells[8]) >= max(start, end)


def test_price_changes_rejects_bad_period(server):
    with pytest.raises(ToolError, match="Unknown period"):
        asyncio.run(server.call_tool("get_price_changes", {"securities": ["SPX Index"], "periods": ["5Y"]}))


def _row(text, label):
    return next(l for l in text.splitlines() if l.startswith(label + ",")).split(",")


def test_price_changes_invert(server):
    args = dict(securities=["USDCAD Curncy"], start_date="2026-03-02", end_date="2026-06-30")
    raw = _row(call(server, "get_price_changes", **args), "USDCAD Curncy")
    inv = _row(call(server, "get_price_changes", invert=True, **args), "1/(USDCAD Curncy)")
    assert float(inv[2]) == pytest.approx(1 / float(raw[2]), rel=1e-5)
    assert float(inv[7]) == pytest.approx((float(raw[2]) / float(raw[4]) - 1) * 100, abs=1e-3)


def test_price_changes_ratio_and_bps(server, svc):
    text = call(server, "get_price_changes", securities=["AAPL US Equity"], ratio_to="SPX Index", periods=["1M"])
    assert "divided by SPX Index" in text
    assert _row(text, "AAPL US Equity / SPX Index")
    assert svc.budget.used_today() == 2  # security + benchmark
    bps = call(server, "get_price_changes", securities=["USGG10YR Index"], periods=["1M"], change_in_bps=True)
    plain = call(server, "get_price_changes", securities=["USGG10YR Index"], periods=["1M"])
    assert float(_row(bps, "USGG10YR Index")[4]) == pytest.approx(float(_row(plain, "USGG10YR Index")[4]) * 100)


def test_historical_has_server_computed_change_and_dividend_default(server, backend):
    text = call(server, "get_historical_data", securities=["SPX Index"], fields=["PX_LAST"],
                start_date="2025-12-31", end_date="2026-06-30", periodicity="MONTHLY")
    assert "split-adjusted, dividend-adjusted" in text
    line = next(l for l in text.splitlines() if l.startswith("CHANGE PX_LAST"))
    assert "pct" in line
    params = backend.calls[-1][2]
    assert params["adjustmentNormal"] is True and params["adjustmentFollowDPDF"] is False
