"""Backend interface.

A backend executes one Bloomberg request and returns the response messages as
plain Python dicts, in the shape produced by blpapi's ``Message.toPy()``.
Request builders and response parsers in ``bbg_mcp.ops`` are pure Python on
top of this, so they can be tested without a Terminal.
"""

from __future__ import annotations

from typing import Any, Protocol

REFDATA = "//blp/refdata"
INSTRUMENTS = "//blp/instruments"
APIFLDS = "//blp/apiflds"


class BloombergError(RuntimeError):
    """Bloomberg returned an error or could not be reached. Message is model-facing."""


class Backend(Protocol):
    def request(self, service: str, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Send one request and return every response message as a dict."""
        ...

    def status(self) -> dict[str, Any]:
        """Report connectivity without spending data hits."""
        ...

    def close(self) -> None: ...
