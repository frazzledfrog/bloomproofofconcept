from pathlib import Path

import pytest

from bbg_mcp.config import load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.toml"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("BBG_MCP_CONFIG", "BBG_MCP_BACKEND", "BBG_MCP_HOST", "BBG_MCP_PORT", "BBG_MCP_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.backend == "blpapi"
    assert cfg.daily_hit_budget == 20000
    assert cfg.news_topics["energy"][0] == "CO1 Comdty"
    assert set(cfg.news_topics) >= {"markets", "economics", "energy", "tech", "fx"}
    assert cfg.news_signal_fields[0] == "NEWS_HEAT_READ_DMAX"
    assert cfg.state_dir == Path("~/.bbg_mcp").expanduser()


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("max_secruities = 5\n")
    with pytest.raises(ValueError, match="max_secruities"):
        load_config(p)


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('backend = "blpapi"\nport = 8194\n')
    monkeypatch.setenv("BBG_MCP_CONFIG", str(p))
    monkeypatch.setenv("BBG_MCP_BACKEND", "mock")
    monkeypatch.setenv("BBG_MCP_PORT", "9000")
    cfg = load_config()
    assert cfg.backend == "mock" and cfg.port == 9000
