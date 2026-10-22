"""Configuration: defaults, optional YAML file, environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_STATE_DIR = Path.home() / ".bbg_mcp"

# Topic -> securities whose news activity represents that topic. Used by the
# "signals" news mode. Mnemonics and baskets are deliberately easy to edit.
DEFAULT_NEWS_TOPICS: dict[str, list[str]] = {
    "markets": ["SPX Index", "INDU Index", "CCMP Index", "SX5E Index", "NKY Index"],
    "economics": ["USGG10YR Index", "CPI YOY Index", "NFP TCH Index", "DXY Curncy"],
    "energy": ["CO1 Comdty", "CL1 Comdty", "NG1 Comdty", "XOM US Equity", "SHEL LN Equity"],
    "metals": ["XAU Curncy", "XAG Curncy", "HG1 Comdty", "BHP AU Equity"],
    "tech": ["AAPL US Equity", "MSFT US Equity", "NVDA US Equity", "GOOGL US Equity", "AMZN US Equity"],
    "financials": ["JPM US Equity", "GS US Equity", "HSBA LN Equity", "BAC US Equity"],
    "fx": ["EURUSD Curncy", "USDJPY Curncy", "GBPUSD Curncy", "USDCNH Curncy"],
    "crypto": ["XBTUSD Curncy", "XETUSD Curncy"],
}

DEFAULT_NEWS_SIGNAL_FIELDS = [
    "NEWS_HEAT_READ_DMAX",
    "NEWS_HEAT_PUB_DAVG",
    "NEWS_SENTIMENT_DAILY_AVG",
]


@dataclass
class Config:
    backend: str = "blpapi"  # "blpapi" or "mock"
    host: str = "localhost"
    port: int = 8194
    timeout_seconds: float = 30.0

    max_securities: int = 25
    max_fields: int = 10
    max_history_days: int = 366
    max_intraday_days: int = 140
    max_rows_per_security: int = 400

    daily_hit_budget: int = 20000
    state_dir: Path = DEFAULT_STATE_DIR

    cache_ttl_history: int = 6 * 3600
    cache_ttl_reference: int = 60
    cache_ttl_news: int = 120

    # "signals": rank topic baskets by news heat/sentiment fields.
    # "disabled": news tools explain that news is not configured.
    news_mode: str = "signals"
    news_topics: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_NEWS_TOPICS))
    news_signal_fields: list[str] = field(default_factory=lambda: list(DEFAULT_NEWS_SIGNAL_FIELDS))


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Build a Config from defaults, then the YAML file, then env vars."""
    cfg = Config()
    path = path or os.environ.get("BBG_MCP_CONFIG")
    if path:
        data = yaml.safe_load(Path(path).read_text()) or {}
        for key, value in data.items():
            if not hasattr(cfg, key):
                raise ValueError(f"Unknown config key: {key}")
            if key == "state_dir":
                value = Path(value).expanduser()
            setattr(cfg, key, value)

    if backend := os.environ.get("BBG_MCP_BACKEND"):
        cfg.backend = backend
    if host := os.environ.get("BBG_MCP_HOST"):
        cfg.host = host
    if port := os.environ.get("BBG_MCP_PORT"):
        cfg.port = int(port)
    if state_dir := os.environ.get("BBG_MCP_STATE_DIR"):
        cfg.state_dir = Path(state_dir).expanduser()

    if cfg.backend not in ("blpapi", "mock"):
        raise ValueError(f"backend must be 'blpapi' or 'mock', got {cfg.backend!r}")
    if cfg.news_mode not in ("signals", "disabled"):
        raise ValueError(f"news_mode must be 'signals' or 'disabled', got {cfg.news_mode!r}")
    return cfg
