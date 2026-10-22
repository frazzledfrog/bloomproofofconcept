"""MCP server wiring. Run with `bbg-mcp` (stdio transport)."""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .backends import BloombergError, make_backend
from .config import Config, load_config
from .guards import GuardError
from .service import BloombergService

INSTRUCTIONS = """\
Bloomberg data from the user's own logged-in Terminal (Desktop API).
- Securities use Bloomberg syntax: '<ticker> [<exchange>] <Yellow Key>', e.g. 'AAPL US Equity',
  'SPX Index', 'CO1 Comdty', 'EURUSD Curncy', 'USGG10YR Index'. If unsure, call search_securities first.
- Field mnemonics look like PX_LAST, PX_VOLUME, CUR_MKT_CAP. If unsure, call search_fields.
- History is limited to roughly the last year. Every call uses the user's Bloomberg data allowance:
  request only what you need and reuse earlier results instead of re-requesting.
- News tools return news heat/sentiment signals, not headlines.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

Securities = Annotated[list[str], Field(description="Bloomberg securities, e.g. ['AAPL US Equity', 'SPX Index']")]
Fields = Annotated[list[str], Field(description="Field mnemonics, e.g. ['PX_LAST', 'PX_VOLUME']")]


def _guarded(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except (GuardError, BloombergError) as exc:
        raise ToolError(str(exc)) from exc


def build_server(svc: BloombergService) -> MCPServer:
    mcp = MCPServer("bloomberg", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    def bbg_status() -> str:
        """Check whether the Bloomberg Terminal connection is up and how much of today's hit budget is used."""
        return svc.status()

    @mcp.tool(annotations=READ_ONLY)
    def get_historical_data(
        securities: Securities,
        fields: Fields,
        start_date: Annotated[str | None, Field(description="YYYY-MM-DD; default one year before end_date")] = None,
        end_date: Annotated[str | None, Field(description="YYYY-MM-DD; default today")] = None,
        periodicity: Annotated[str, Field(description="DAILY, WEEKLY, MONTHLY or QUARTERLY")] = "DAILY",
        adjust_splits: bool = True,
        adjust_dividends: Annotated[bool, Field(description="Adjust prices for dividends (total-return style)")] = False,
        currency: Annotated[str | None, Field(description="Convert to this ISO currency, e.g. USD")] = None,
        max_rows: Annotated[int | None, Field(description="Cap rows per security (most recent kept)")] = None,
    ) -> str:
        """Historical time series (end-of-day) for up to ~1 year. Returns CSV per security."""
        return _guarded(
            lambda: svc.historical(
                securities, fields, start_date, end_date, periodicity, adjust_splits, adjust_dividends, currency, max_rows
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_reference_data(securities: Securities, fields: Fields) -> str:
        """Current snapshot values (last price, name, market cap, ratios...). Returns CSV."""
        return _guarded(lambda: svc.reference(securities, fields))

    @mcp.tool(annotations=READ_ONLY)
    def get_intraday_bars(
        security: Annotated[str, Field(description="One Bloomberg security")],
        interval_minutes: Annotated[int, Field(description="Bar size in minutes, 1-1440")] = 60,
        start_datetime: Annotated[str | None, Field(description="UTC, YYYY-MM-DDTHH:MM; default 5 days ago")] = None,
        end_datetime: Annotated[str | None, Field(description="UTC, YYYY-MM-DDTHH:MM; default now")] = None,
        event_type: Annotated[str, Field(description="TRADE, BID or ASK")] = "TRADE",
    ) -> str:
        """Intraday OHLCV bars. Bloomberg keeps about 140 days of intraday history."""
        return _guarded(lambda: svc.intraday(security, interval_minutes, start_datetime, end_datetime, event_type))

    @mcp.tool(annotations=READ_ONLY)
    def search_securities(
        query: Annotated[str, Field(description="Company name, ticker or description, e.g. 'brent crude'")],
        yellow_key: Annotated[
            str | None, Field(description="Optional filter: equity, index, comdty, curncy, govt, corp, mtge, muni, pfd")
        ] = None,
        max_results: int = 10,
    ) -> str:
        """Find Bloomberg security identifiers from a name or partial ticker."""
        return _guarded(lambda: svc.search_securities(query, yellow_key, max_results))

    @mcp.tool(annotations=READ_ONLY)
    def search_fields(
        query: Annotated[str, Field(description="Words describing the data, e.g. 'dividend yield'")],
        max_results: int = 15,
    ) -> str:
        """Find Bloomberg field mnemonics from a description."""
        return _guarded(lambda: svc.search_fields(query, max_results))

    @mcp.tool(annotations=READ_ONLY)
    def get_news_signals(
        topic: Annotated[str, Field(description="A topic from the bbg://news-topics resource, e.g. energy, tech, fx")],
    ) -> str:
        """Which instruments in a topic are drawing the most news right now (news heat and sentiment)."""
        return _guarded(lambda: svc.news_signals(topic))

    @mcp.tool(annotations=READ_ONLY)
    def get_security_news_signals(securities: Securities) -> str:
        """News heat and sentiment for specific securities."""
        return _guarded(lambda: svc.security_news_signals(securities))

    @mcp.resource("bbg://news-topics", name="news-topics", mime_type="text/plain")
    def news_topics() -> str:
        """Configured news topics and the securities that represent each."""
        return svc.news_topics()

    @mcp.prompt()
    def market_brief(topic: str = "markets") -> str:
        """Brief on a topic: recent price action plus where news activity is concentrated."""
        return (
            f"Using the Bloomberg tools, write a short brief on '{topic}'. Read the bbg://news-topics resource "
            f"for the securities in this topic, call get_news_signals for '{topic}', then get_historical_data "
            "(PX_LAST, WEEKLY, last year) for the top few names by news heat. Summarise the year's moves and "
            "flag where news activity is unusually high. Keep tool calls to a minimum."
        )

    return mcp


def main() -> None:
    cfg: Config = load_config()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    # stdout is the MCP stdio channel: log to a file (and stderr) only.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(cfg.state_dir / "bbg_mcp.log"), logging.StreamHandler(sys.stderr)],
    )
    backend: Any = make_backend(cfg)
    try:
        build_server(BloombergService(cfg, backend)).run("stdio")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
