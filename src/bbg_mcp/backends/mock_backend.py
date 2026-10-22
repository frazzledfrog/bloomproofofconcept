"""Synthetic backend for development and tests without a Terminal.

Responses mimic the ``Message.toPy()`` shapes of the real services. Prices are
a deterministic random walk seeded by the security name, so any ticker works.
Securities containing "INVALID" return a securityError.
"""

from __future__ import annotations

import random
import zlib
from datetime import date, datetime, timedelta
from typing import Any

from .base import APIFLDS, INSTRUMENTS, REFDATA, BloombergError

_INSTRUMENTS = [
    ("AAPL US<equity>", "Apple Inc (U.S.)"),
    ("MSFT US<equity>", "Microsoft Corp (U.S.)"),
    ("NVDA US<equity>", "NVIDIA Corp (U.S.)"),
    ("SPX<index>", "S&P 500 INDEX"),
    ("CO1<cmdty>", "Generic 1st 'CO' Future (Brent Crude)"),
    ("CL1<cmdty>", "Generic 1st 'CL' Future (WTI Crude)"),
    ("EURUSD<curncy>", "EUR-USD X-RATE"),
]

_FIELDS = [
    ("PR005", "PX_LAST", "Last Price", "Float64"),
    ("PR006", "PX_OPEN", "Open Price", "Float64"),
    ("PR013", "PX_VOLUME", "Volume", "Int64"),
    ("DS002", "NAME", "Name", "String"),
    ("RR902", "CUR_MKT_CAP", "Current Market Cap", "Float64"),
    ("NS001", "NEWS_HEAT_READ_DMAX", "News Heat - Max Daily Readership", "Float64"),
    ("NS002", "NEWS_HEAT_PUB_DAVG", "News Heat - Average Daily Publication", "Float64"),
    ("NS003", "NEWS_SENTIMENT_DAILY_AVG", "News Sentiment Daily Average", "Float64"),
]


def _rng(*parts: Any) -> random.Random:
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


def _field_value(security: str, field: str, day: date | None = None) -> Any:
    r = _rng(security, field, day)
    base = 20 + _rng(security).random() * 480
    if field == "NAME":
        return security.split()[0] + " MOCK"
    if field == "PX_VOLUME":
        return int(r.uniform(1e5, 5e7))
    if field.startswith("NEWS_HEAT"):
        return round(r.uniform(0, 4), 2)
    if field.startswith("NEWS_SENTIMENT"):
        return round(r.uniform(-1, 1), 3)
    if field == "CUR_MKT_CAP":
        return round(base * 1e9, 0)
    return round(base * (1 + r.uniform(-0.01, 0.01)), 4)


def _walk(security: str, field: str, days: list[date]) -> list[float]:
    r = _rng(security, field, "walk")
    price = 20 + _rng(security).random() * 480
    out = []
    for _ in days:
        price *= 1 + r.gauss(0.0003, 0.015)
        out.append(round(price, 4))
    return out


def _period_dates(start: date, end: date, periodicity: str) -> list[date]:
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    if periodicity == "WEEKLY":
        days = [d for d in days if d.weekday() == 4]
    elif periodicity == "MONTHLY":
        days = [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]
    elif periodicity == "QUARTERLY":
        days = [
            d for i, d in enumerate(days)
            if d.month % 3 == 0 and (i + 1 == len(days) or days[i + 1].month != d.month)
        ]
    return days


def _security_error(security: str) -> dict[str, Any]:
    return {"source": "mock", "code": 15, "category": "BAD_SEC", "message": "Unknown/Invalid security [nid:mock]", "subcategory": "INVALID_SECURITY"}


class MockBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, service: str, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((service, operation, params))
        handler = {
            (REFDATA, "HistoricalDataRequest"): self._historical,
            (REFDATA, "ReferenceDataRequest"): self._reference,
            (REFDATA, "IntradayBarRequest"): self._intraday,
            (INSTRUMENTS, "instrumentListRequest"): self._instruments,
            (APIFLDS, "FieldSearchRequest"): self._fields,
        }.get((service, operation))
        if handler is None:
            raise BloombergError(f"Mock backend does not implement {service} {operation}.")
        return handler(params)

    def _historical(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        start = datetime.strptime(p["startDate"], "%Y%m%d").date()
        end = datetime.strptime(p["endDate"], "%Y%m%d").date()
        days = _period_dates(start, end, p.get("periodicitySelection", "DAILY"))
        msgs = []
        for seq, sec in enumerate(p["securities"]):
            sd: dict[str, Any] = {"security": sec, "eidData": [], "sequenceNumber": seq, "fieldExceptions": [], "fieldData": []}
            if "INVALID" in sec.upper():
                sd["securityError"] = _security_error(sec)
            else:
                series = {f: _walk(sec, f, days) if f.startswith("PX_") and f != "PX_VOLUME" else None for f in p["fields"]}
                for i, d in enumerate(days):
                    row: dict[str, Any] = {"date": d}
                    for f in p["fields"]:
                        row[f] = series[f][i] if series[f] else _field_value(sec, f, d)
                    sd["fieldData"].append(row)
            msgs.append({"securityData": sd})
        return msgs

    def _reference(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for seq, sec in enumerate(p["securities"]):
            sd: dict[str, Any] = {"security": sec, "eidData": [], "sequenceNumber": seq, "fieldExceptions": [], "fieldData": {}}
            if "INVALID" in sec.upper():
                sd["securityError"] = _security_error(sec)
            else:
                for f in p["fields"]:
                    if f.startswith("BAD_"):
                        sd["fieldExceptions"].append(
                            {"fieldId": f, "errorInfo": {"category": "BAD_FLD", "message": "Field not valid", "subcategory": "INVALID_FIELD"}}
                        )
                    else:
                        sd["fieldData"][f] = _field_value(sec, f, date.today())
            out.append(sd)
        return [{"securityData": out}]

    def _intraday(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        start: datetime = p["startDateTime"]
        end: datetime = p["endDateTime"]
        step = timedelta(minutes=p["interval"])
        r = _rng(p["security"], "intraday")
        price = 20 + _rng(p["security"]).random() * 480
        bars = []
        t = start
        while t < end and len(bars) < 5000:
            if t.weekday() < 5 and 14 <= t.hour < 21:
                o = price
                price *= 1 + r.gauss(0, 0.002)
                bars.append({
                    "time": t, "open": round(o, 4), "high": round(max(o, price) * 1.001, 4),
                    "low": round(min(o, price) * 0.999, 4), "close": round(price, 4),
                    "volume": int(r.uniform(1e3, 1e5)), "numEvents": int(r.uniform(10, 500)), "value": 0.0,
                })
            t += step
        return [{"barData": {"eidData": [], "delayedSecurity": False, "barTickData": bars}}]

    def _instruments(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        q = p["query"].lower()
        hits = [{"security": s, "description": d} for s, d in _INSTRUMENTS if q in s.lower() or q in d.lower()]
        return [{"results": hits[: p.get("maxResults", 10)]}]

    def _fields(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        q = p["searchSpec"].lower()
        hits = [
            {"id": fid, "fieldInfo": {"mnemonic": m, "description": d, "datatype": t, "categoryName": []}}
            for fid, m, d, t in _FIELDS
            if q in m.lower() or q in d.lower()
        ]
        return [{"fieldData": hits}]

    def status(self) -> dict[str, Any]:
        return {"connected": True, "backend": "mock", "note": "Synthetic data, not real Bloomberg data."}

    def close(self) -> None:
        pass
