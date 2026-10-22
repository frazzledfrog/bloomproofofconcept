"""Backend over a live Desktop API session (Terminal on the same machine)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .base import REFDATA, BloombergError

log = logging.getLogger(__name__)

try:
    import blpapi
except ImportError:  # pragma: no cover - depends on the host machine
    blpapi = None


def _fill(parent: Any, key: str, value: Any) -> None:
    """Populate a blpapi request element from plain Python values."""
    if isinstance(value, dict):
        child = parent.getElement(key)
        for k, v in value.items():
            _fill(child, k, v)
    elif isinstance(value, (list, tuple)):
        child = parent.getElement(key)
        for item in value:
            if isinstance(item, dict):
                entry = child.appendElement()
                for k, v in item.items():
                    _fill(entry, k, v)
            else:
                child.appendValue(item)
    else:
        parent.setElement(key, value)


class BlpapiBackend:
    def __init__(self, host: str, port: int, timeout_seconds: float):
        if blpapi is None:
            raise BloombergError(
                "The blpapi package is not installed. Install it from Bloomberg's index: "
                "pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi"
            )
        self._host = host
        self._port = port
        self._timeout = timeout_seconds
        self._session: Any = None
        self._services: set[str] = set()
        self._lock = threading.Lock()

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        opts = blpapi.SessionOptions()
        opts.setServerHost(self._host)
        opts.setServerPort(self._port)
        session = blpapi.Session(opts)
        if not session.start():
            raise BloombergError(
                f"Could not connect to the Bloomberg Desktop API at {self._host}:{self._port}. "
                "Make sure the Terminal is running and logged in on this machine."
            )
        self._session = session
        self._services.clear()
        return session

    def _ensure_service(self, session: Any, service: str) -> Any:
        if service not in self._services:
            if not session.openService(service):
                raise BloombergError(f"Could not open Bloomberg service {service} (not entitled or unavailable).")
            self._services.add(service)
        return session.getService(service)

    def _reset(self) -> None:
        if self._session is not None:
            try:
                self._session.stop()
            except Exception:  # noqa: BLE001 - best effort on a dead session
                pass
        self._session = None
        self._services.clear()

    def request(self, service: str, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._lock:
            try:
                return self._request(service, operation, params)
            except BloombergError:
                raise
            except Exception as exc:
                # A session-level failure (Terminal closed, logged out): drop the
                # session so the next call reconnects.
                log.exception("blpapi request failed")
                self._reset()
                raise BloombergError(f"Bloomberg request failed: {exc}") from exc

    def _request(self, service: str, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        session = self._ensure_session()
        svc = self._ensure_service(session, service)
        req = svc.createRequest(operation)
        root = req.asElement()
        for key, value in params.items():
            _fill(root, key, value)

        cid = session.sendRequest(req)
        deadline = time.monotonic() + self._timeout
        messages: list[dict[str, Any]] = []
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise BloombergError(f"Bloomberg request timed out after {self._timeout:.0f}s.")
            event = session.nextEvent(min(remaining_ms, 1000))
            etype = event.eventType()
            if etype == blpapi.Event.TIMEOUT:
                continue
            done = False
            for msg in event:
                mtype = str(msg.messageType())
                if mtype in ("SessionTerminated", "SessionConnectionDown"):
                    self._reset()
                    raise BloombergError("Bloomberg session dropped. Check the Terminal is still logged in, then retry.")
                if cid not in msg.correlationIds():
                    continue
                if mtype == "RequestFailure":
                    reason = msg.toPy().get("reason", {})
                    raise BloombergError(f"Bloomberg request failed: {reason.get('description') or reason}")
                messages.append(msg.toPy())
                if etype == blpapi.Event.RESPONSE:
                    done = True
            if done:
                return messages

    def status(self) -> dict[str, Any]:
        with self._lock:
            try:
                session = self._ensure_session()
                self._ensure_service(session, REFDATA)
            except Exception as exc:  # noqa: BLE001 - status reports, never raises
                self._reset()
                return {"connected": False, "host": self._host, "port": self._port, "error": str(exc)}
            return {"connected": True, "host": self._host, "port": self._port, "blpapi_version": blpapi.__version__}

    def can_open(self, service: str) -> bool:
        """Whether this login can open a service (used by the probe script)."""
        with self._lock:
            try:
                self._ensure_service(self._ensure_session(), service)
                return True
            except BloombergError:
                return False

    def close(self) -> None:
        with self._lock:
            self._reset()
