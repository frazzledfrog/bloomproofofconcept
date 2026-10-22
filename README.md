# bbg-mcp

An MCP server that gives a local AI client access to Bloomberg data through the **Desktop API (DAPI)**. DAPI rides on the Bloomberg Terminal you already have open: the server talks to `localhost:8194`, the Terminal handles authentication and entitlements, and no B-PIPE or server licence is involved.

## Prerequisites

- A Bloomberg Terminal **running and logged in on the same machine** as this server. In practice that means Windows.
- API access enabled for your login. `WAPI <GO>` in the Terminal shows API status and your usage against data limits.
- Python 3.11 or newer.

## Install

```powershell
git clone <this repo>; cd bloomproofofconcept
python -m venv .venv; .venv\Scripts\activate
pip install -e .
pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ "blpapi>=3.26"
copy config.example.toml config.toml   # optional; edit to taste
```

`blpapi` is not on PyPI, so it has to come from Bloomberg's own package index as shown above. Version 3.26 and later ship one Windows wheel for any Python 3.10+, so no compiler is needed.

Keep the project (or at least `.venv`) outside OneDrive if you can. OneDrive sync can lock or partially upload venv files, which causes confusing import errors.

## Check the connection before connecting an AI

```powershell
python scripts\smoke_test.py      # status, 1y history, snapshot, lookups, news signals
python scripts\probe_news.py      # which news fields/services your entitlements expose
```

## Connect a client

The server speaks MCP over stdio. For Claude Desktop, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bloomberg": {
      "command": "C:\\path\\to\\bloomproofofconcept\\.venv\\Scripts\\bbg-mcp.exe",
      "env": { "BBG_MCP_CONFIG": "C:\\path\\to\\bloomproofofconcept\\config.toml" }
    }
  }
}
```

Local-LLM hosts that support MCP (LM Studio, or an MCP-capable front end for Ollama such as Open WebUI via `mcpo`) take the same command and environment. Use a model that is good at tool calling. Small models often get Bloomberg ticker syntax wrong, and the server's instructions and the `search_securities` tool are there to compensate.

To develop without a Terminal, set `BBG_MCP_BACKEND=mock` to get deterministic synthetic data.

## Tools

| Tool | Purpose |
|---|---|
| `bbg_status` | Connection state and today's approximate hit usage |
| `get_historical_data` | EOD history spanning up to 366 days, from any year (DAILY/WEEKLY/MONTHLY/QUARTERLY), returned as CSV with server-computed first-to-last CHANGE lines |
| `get_price_changes` | % and absolute change for prices, indices, FX and yields over 1D/1W/2W/1M/3M/6M/YTD/1Y or a custom range. All arithmetic is server-side, with options for inverted FX (1/x), a ratio to a benchmark (relative performance, cross rates) and basis points for yields |
| `get_reference_data` | Current snapshot fields |
| `get_intraday_bars` | OHLCV bars (Bloomberg keeps about 140 days) |
| `search_securities` | Turn a name like "brent crude" into an identifier like `CO1 Comdty` |
| `search_fields` | Turn a description like "dividend yield" into a field mnemonic |
| `get_news_signals` | Rank a topic's instruments by news heat and sentiment |
| `get_security_news_signals` | News heat and sentiment for given securities |

History is split- and dividend-adjusted by default, using exactly the adjustments requested rather than your Terminal's `DPDF <GO>` settings. Price indices such as `SPX Index` contain no dividends; ask for the total-return ticker (e.g. `SPXT Index`) for total return.

There is also the resource `bbg://news-topics`, which lists the topic baskets, and the prompts `market_brief` and `market_moves` (FX and index move tables).

## About news

A standard Terminal DAPI login does not reliably expose headlines or story text. Those usually need a B-PIPE or Data License news entitlement. This server therefore provides **news activity signals** (Bloomberg's news heat and sentiment fields) by topic and by security, which reliably tells the model *where* the news is. To read the stories themselves, use `TOP <GO>` or `<ticker> CN <GO>` in the Terminal. Run `scripts/probe_news.py` to see what your login actually exposes. If it finds a headline mechanism, that can be added as a new backend operation.

Edit the topics, and which instruments represent each topic, under `[news_topics]` in `config.toml`.

## Data limits and guardrails

Bloomberg enforces daily and monthly per-user data limits on DAPI. Hitting them cuts off your API data until the limit resets. To prevent that, the server:

- caps securities, fields, and date windows per call, and refuses (with guidance to the model) rather than silently truncating;
- keeps an approximate daily hit counter (security x field per request) in `~/.bbg_mcp/usage.sqlite3` and refuses once `daily_hit_budget` is reached;
- caches responses (history for 6h, snapshots for 60s, news for 2 min), so repeated questions do not re-spend hits.

The counter is an estimate. `WAPI <GO>` is the authoritative usage figure.

## Licensing

DAPI data is licensed for use by you, on your machine. A fully local model keeps the data on the box. If you connect a cloud-hosted model (including Claude Desktop), the returned data leaves the machine. Check that use with your firm's Bloomberg agreement and compliance team before doing it.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest
BBG_MCP_BACKEND=mock npx @modelcontextprotocol/inspector .venv/bin/bbg-mcp
```

Layout: `ops.py` builds requests and parses responses (plain dicts in `Message.toPy()` shape). `backends/` holds the live `blpapi` session and the mock. `guards.py` holds the limits, hit budget, and cache. `service.py` holds the tool logic. `server.py` holds the MCP wiring.
