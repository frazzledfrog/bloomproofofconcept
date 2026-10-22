import pytest

from bbg_mcp.backends.mock_backend import MockBackend
from bbg_mcp.config import Config
from bbg_mcp.service import BloombergService


@pytest.fixture
def cfg(tmp_path):
    return Config(backend="mock", state_dir=tmp_path)


@pytest.fixture
def backend():
    return MockBackend()


@pytest.fixture
def svc(cfg, backend):
    return BloombergService(cfg, backend)
