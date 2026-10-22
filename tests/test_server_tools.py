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
