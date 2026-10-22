from __future__ import annotations

from ..config import Config
from .base import Backend, BloombergError


def make_backend(cfg: Config) -> Backend:
    if cfg.backend == "mock":
        from .mock_backend import MockBackend

        return MockBackend()
    from .blpapi_backend import BlpapiBackend

    return BlpapiBackend(cfg.host, cfg.port, cfg.timeout_seconds)


__all__ = ["Backend", "BloombergError", "make_backend"]
