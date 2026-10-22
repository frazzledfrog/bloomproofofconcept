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
- One history request may span at most about one year (any year, e.g. 2008 is fine). Every call uses
  the user's Bloomberg data allowance: request only what you need and reuse earlier results.
- Never do arithmetic on prices yourself. For any change, return, % move, FX move, cross rate or
  relative performance, call get_price_changes and quote its numbers. get_historical_data also ends
  each security with server-computed CHANGE lines; quote those rather than dividing yourself.
- Prices are split- and dividend-adjusted by default. Indices like 'SPX Index' are price indices
  (dividends are not in them); for total return use the total-return ticker, e.g. 'SPXT Index'.
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
        adjust_dividends: Annotated[
            bool, Field(description="Adjust prices for dividends (default true; has no effect on price indices)")
        ] = True,
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
    def get_price_changes(
        securities: Securities,
        periods: Annotated[
            list[str] | None,
            Field(description="Any of 1D, 1W, 2W, 1M, 3M, 6M, YTD, 1Y; default 1D,1W,1M,3M,YTD,1Y"),
        ] = None,
        start_date: Annotated[
            str | None, Field(description="YYYY-MM-DD; use with end_date for one custom range instead of periods")
        ] = None,
        end_date: Annotated[str | None, Field(description="YYYY-MM-DD; default today")] = None,
        field: Annotated[str, Field(description="Field to measure; default PX_LAST")] = "PX_LAST",
        currency: Annotated[str | None, Field(description="Convert to this ISO currency first, e.g. CAD")] = None,
        adjust_dividends: Annotated[bool, Field(description="Dividend-adjusted prices (default true)")] = True,
        invert: Annotated[
            bool, Field(description="Measure 1/x, e.g. USDCAD Curncy -> CAD in USD terms (how much CAD moved)")
        ] = False,
        ratio_to: Annotated[
            str | None,
            Field(description="Divide each security by this one on every date: relative performance "
                  "(AAPL US Equity vs SPX Index) or cross rates"),
        ] = None,
        change_in_bps: Annotated[
            bool, Field(description="Report absolute change in basis points; use for yields, e.g. USGG10YR Index")
        ] = False,
    ) -> str:
        """Change in prices, index levels, FX rates or yields over standard periods or a custom date range.

        Use this for ANY question about a change, return or move; all arithmetic (division, % change,
        inversion, ratios, bps) is done here so you can quote the numbers directly. For calendar year
        2024 pass start_date 2024-01-01 and end_date 2024-12-31 (measured from the prior year-end close).
        FX tickers look like 'USDCAD Curncy'. To express a currency's own move against USD when the
        ticker quotes USD first (USDCAD, USDJPY), set invert=true.
        """
        return _guarded(
            lambda: svc.price_changes(
                securities, periods, start_date, end_date, field, currency,
                adjust_dividends, invert, ratio_to, change_in_bps,
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

    @mcp.prompt()
    def market_moves(
        fx: str = "USDCAD Curncy, EURUSD Curncy, USDJPY Curncy, GBPUSD Curncy",
        indices: str = "SPX Index, SPTSX Index, SX5E Index, NKY Index, USGG10YR Index",
    ) -> str:
        """Table of FX and index moves over standard periods."""
        return (
            f"Call get_price_changes once for these FX pairs: {fx}; and once for these indices/rates: {indices}. "
            "Present two tables (FX, then indices/rates) with 1D, 1W, 1M, 3M, YTD and 1Y % changes; "
            "for yield tickers show the absolute change in basis points (chg x 100) instead of %. "
            "Then give 2-3 sentences on the largest moves. Do not call any other tools."
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
