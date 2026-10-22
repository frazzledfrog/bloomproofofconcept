"""Request builders and response parsers for the Bloomberg operations we use.

Builders return ``(service, operation, params)``; parsers take the list of
response message dicts (``Message.toPy()`` shape) and return plain results.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .backends.base import APIFLDS, INSTRUMENTS, REFDATA, BloombergError

PERIODICITIES = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY")

YELLOW_KEY_FILTERS = {
    "equity": "YK_FILTER_EQTY",
    "index": "YK_FILTER_INDX",
    "comdty": "YK_FILTER_CMDT",
    "curncy": "YK_FILTER_CURR",
    "govt": "YK_FILTER_GOVT",
    "corp": "YK_FILTER_CORP",
    "mtge": "YK_FILTER_MTGE",
    "muni": "YK_FILTER_MUNI",
    "pfd": "YK_FILTER_PRFD",
    "m-mkt": "YK_FILTER_MMKT",
}


def _overrides(overrides: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [{"fieldId": k.upper(), "value": str(v)} for k, v in (overrides or {}).items()]


def _check_response_error(msg: dict[str, Any]) -> None:
    if err := msg.get("responseError"):
        raise BloombergError(f"Bloomberg error: {err.get('message') or err}")


def _field_exceptions(sec: dict[str, Any]) -> list[str]:
    out = []
    for fe in sec.get("fieldExceptions") or []:
        info = fe.get("errorInfo") or {}
        out.append(f"{fe.get('fieldId')}: {info.get('message') or info.get('subcategory') or 'error'}")
    return out


def _security_error(sec: dict[str, Any]) -> str | None:
    err = sec.get("securityError")
    if not err:
        return None
    return err.get("message") or err.get("subcategory") or "security error"


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


# --- HistoricalDataRequest --------------------------------------------------

def build_historical(
    securities: list[str],
    fields: list[str],
    start: date,
    end: date,
    periodicity: str = "DAILY",
    adjust_splits: bool = True,
    adjust_dividends: bool = False,
    currency: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    params: dict[str, Any] = {
        "securities": securities,
        "fields": fields,
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        "periodicitySelection": periodicity,
        "adjustmentSplit": adjust_splits,
        "adjustmentAbnormal": adjust_dividends,
        "adjustmentNormal": adjust_dividends,
    }
    if currency:
        params["currency"] = currency.upper()
    if ov := _overrides(overrides):
        params["overrides"] = ov
    return REFDATA, "HistoricalDataRequest", params


def parse_historical(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per security: {security, rows, error, field_exceptions}."""
    by_security: dict[str, dict[str, Any]] = {}
    for msg in messages:
        _check_response_error(msg)
        sec = msg.get("securityData")
        if not sec:
            continue
        name = sec.get("security", "?")
        entry = by_security.setdefault(name, {"security": name, "rows": [], "error": None, "field_exceptions": []})
        entry["error"] = entry["error"] or _security_error(sec)
        entry["field_exceptions"].extend(_field_exceptions(sec))
        for row in sec.get("fieldData") or []:
            entry["rows"].append(_plain(row))
    return list(by_security.values())


# --- ReferenceDataRequest ---------------------------------------------------

def build_reference(
    securities: list[str], fields: list[str], overrides: dict[str, Any] | None = None
) -> tuple[str, str, dict[str, Any]]:
    params: dict[str, Any] = {"securities": securities, "fields": fields}
    if ov := _overrides(overrides):
        params["overrides"] = ov
    return REFDATA, "ReferenceDataRequest", params


def parse_reference(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per security: {security, data, error, field_exceptions}."""
    out = []
    for msg in messages:
        _check_response_error(msg)
        for sec in msg.get("securityData") or []:
            out.append(
                {
                    "security": sec.get("security", "?"),
                    "data": _plain(sec.get("fieldData") or {}),
                    "error": _security_error(sec),
                    "field_exceptions": _field_exceptions(sec),
                }
            )
    return out


# --- IntradayBarRequest -----------------------------------------------------

def build_intraday(
    security: str, interval_minutes: int, start: datetime, end: datetime, event_type: str = "TRADE"
) -> tuple[str, str, dict[str, Any]]:
    params = {
        "security": security,
        "eventType": event_type,
        "interval": interval_minutes,
        "startDateTime": start,
        "endDateTime": end,
    }
    return REFDATA, "IntradayBarRequest", params


def parse_intraday(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for msg in messages:
        _check_response_error(msg)
        bar_data = msg.get("barData") or {}
        for bar in bar_data.get("barTickData") or []:
            bars.append(_plain({k: bar.get(k) for k in ("time", "open", "high", "low", "close", "volume", "numEvents")}))
    return bars


# --- //blp/instruments security lookup --------------------------------------

def build_security_search(query: str, yellow_key: str | None, max_results: int) -> tuple[str, str, dict[str, Any]]:
    params: dict[str, Any] = {"query": query, "maxResults": max_results}
    if yellow_key:
        yk = YELLOW_KEY_FILTERS.get(yellow_key.lower())
        if yk is None:
            raise BloombergError(f"Unknown yellow_key {yellow_key!r}; use one of {sorted(YELLOW_KEY_FILTERS)}.")
        params["yellowKeyFilter"] = yk
    return INSTRUMENTS, "instrumentListRequest", params


_YK_RE = re.compile(r"<([a-z-]+)>$", re.IGNORECASE)

# Instrument search spells some yellow keys differently from refdata.
_YELLOW_KEYS = {"cmdty": "Comdty", "m-mkt": "M-Mkt"}


def to_api_ticker(security: str) -> str:
    """'AAPL US<equity>' -> 'AAPL US Equity' (the form refdata requests expect)."""
    m = _YK_RE.search(security)
    if not m:
        return security
    key = m.group(1).lower()
    return f"{security[: m.start()].strip()} {_YELLOW_KEYS.get(key, key.capitalize())}"


def parse_security_search(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for msg in messages:
        _check_response_error(msg)
        for r in msg.get("results") or []:
            out.append({"security": to_api_ticker(r.get("security", "")), "description": r.get("description", "")})
    return out


# --- //blp/apiflds field search ---------------------------------------------

def build_field_search(query: str) -> tuple[str, str, dict[str, Any]]:
    return APIFLDS, "FieldSearchRequest", {"searchSpec": query, "returnFieldDocumentation": False}


def parse_field_search(messages: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    out = []
    for msg in messages:
        _check_response_error(msg)
        for fd in msg.get("fieldData") or []:
            info = fd.get("fieldInfo")
            if not info:
                continue
            out.append(
                {
                    "mnemonic": info.get("mnemonic", ""),
                    "description": info.get("description", ""),
                    "datatype": str(info.get("datatype", "")),
                }
            )
            if len(out) >= limit:
                return out
    return out
