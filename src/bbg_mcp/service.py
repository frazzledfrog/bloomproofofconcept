"""Tool logic: guards, budget, cache, backend call, formatting.

Kept separate from the MCP wiring so it can be tested and scripted directly.
Methods return model-facing text and raise GuardError/BloombergError with
model-facing messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import changes, ops
from .backends.base import Backend, BloombergError
from .config import Config
from .guards import GuardError, HitBudget, TTLCache, normalize_fields, normalize_list, resolve_window


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    s = str(value)
    return f'"{s}"' if "," in s else s


def _issues(entry: dict[str, Any]) -> list[str]:
    lines = []
    if entry.get("error"):
        lines.append(f"  ERROR: {entry['error']}")
    for fe in entry.get("field_exceptions") or []:
        lines.append(f"  FIELD ERROR: {fe}")
    return lines


def _adjustment_label(splits: bool, dividends: bool) -> str:
    parts = [("split-adjusted" if splits else "not split-adjusted"),
             ("dividend-adjusted" if dividends else "not dividend-adjusted")]
    return ", ".join(parts)


class BloombergService:
    def __init__(self, cfg: Config, backend: Backend, budget: HitBudget | None = None, cache: TTLCache | None = None):
        self.cfg = cfg
        self.backend = backend
        self.budget = budget or HitBudget(cfg.state_dir / "usage.sqlite3", cfg.daily_hit_budget)
        self.cache = cache or TTLCache()

    def _cached(self, key: str, ttl: float, hits: int, fetch):
        if (hit := self.cache.get(key)) is not None:
            return hit
        self.budget.charge(hits)
        value = fetch()
        self.cache.set(key, value, ttl)
        return value

    # --- status ---------------------------------------------------------

    def status(self) -> str:
        st = self.backend.status()
        lines = [f"{k}: {v}" for k, v in st.items()]
        lines.append(f"hits_used_today (approx): {self.budget.used_today()} / {self.budget.limit}")
        lines.append(f"max_history_days: {self.cfg.max_history_days}")
        return "\n".join(lines)

    # --- historical -----------------------------------------------------

    def historical(
        self,
        securities: list[str] | str,
        fields: list[str] | str,
        start_date: str | None = None,
        end_date: str | None = None,
        periodicity: str = "DAILY",
        adjust_splits: bool = True,
        adjust_dividends: bool = True,
        currency: str | None = None,
        max_rows: int | None = None,
    ) -> str:
        secs = normalize_list(securities, "security", self.cfg.max_securities)
        flds = normalize_fields(fields, self.cfg.max_fields)
        periodicity = periodicity.upper()
        if periodicity not in ops.PERIODICITIES:
            raise GuardError(f"periodicity must be one of {ops.PERIODICITIES}.")
        start, end = resolve_window(start_date, end_date, self.cfg.max_history_days, default_days=365)
        max_rows = min(max_rows or self.cfg.max_rows_per_security, self.cfg.max_rows_per_security)

        req = ops.build_historical(secs, flds, start, end, periodicity, adjust_splits, adjust_dividends, currency)
        key = TTLCache.key(*req)
        results = self._cached(
            key,
            self.cfg.cache_ttl_history,
            len(secs) * len(flds),
            lambda: ops.parse_historical(self.backend.request(*req)),
        )

        out = [
            f"Historical {periodicity} {start}..{end}; fields: {', '.join(flds)}; "
            f"{_adjustment_label(adjust_splits, adjust_dividends)}",
            "Each security ends with CHANGE lines computed by the server (first row to last row); "
            "quote them rather than recalculating.",
        ]
        for entry in results:
            rows = entry["rows"]
            out.append("")
            out.append(f"## {entry['security']} ({len(rows)} rows)")
            out.extend(_issues(entry))
            if not rows:
                continue
            shown = rows[-max_rows:]
            if len(shown) < len(rows):
                out.append(
                    f"  NOTE: showing last {len(shown)} of {len(rows)} rows; "
                    "use a coarser periodicity or shorter window for the full range."
                )
            out.append(",".join(["date", *flds]))
            for row in shown:
                out.append(",".join([_fmt(row.get("date"))] + [_fmt(row.get(f)) for f in flds]))
            for f in flds:
                obs = changes.observations(rows, f)
                if len(obs) >= 2:
                    (d0, v0), (d1, v1) = obs[0], obs[-1]
                    abs_chg, pct = changes.change(v0, v1)
                    out.append(
                        f"CHANGE {f}: {_fmt(v0)} on {d0} -> {_fmt(v1)} on {d1}; "
                        f"abs {_fmt(abs_chg)}; pct {_fmt(None if pct is None else round(pct, 3))}%"
                    )
        return "\n".join(out)

    # --- period changes -------------------------------------------------

    def price_changes(
        self,
        securities: list[str] | str,
        periods: list[str] | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        field: str = "PX_LAST",
        currency: str | None = None,
        adjust_dividends: bool = True,
        invert: bool = False,
        ratio_to: str | None = None,
        change_in_bps: bool = False,
    ) -> str:
        secs = normalize_list(securities, "security", self.cfg.max_securities)
        fld = normalize_fields(field, 1)[0]
        bench = normalize_list(ratio_to, "ratio_to security", 1)[0] if ratio_to else None
        # Fetch a little before the earliest anchor so a close "as of" that date exists
        # even when it falls on a weekend or holiday.
        pad = timedelta(days=10)

        if start_date:
            if periods:
                raise GuardError("Pass either periods or start_date/end_date, not both.")
            start, end = resolve_window(start_date, end_date, self.cfg.max_history_days, default_days=365)
            fetch_start = start - pad
            plist: list[str] = []
        else:
            plist = [p.upper() for p in normalize_list(periods or list(changes.DEFAULT_PERIODS), "period", 8)]
            if bad := [p for p in plist if p not in changes.PERIODS]:
                raise GuardError(f"Unknown period(s) {bad}; use any of {changes.PERIODS}.")
            _, end = resolve_window(None, end_date, self.cfg.max_history_days, default_days=0)
            anchors = [changes.anchor_date(p, end) or end - timedelta(days=1) for p in plist]
            fetch_start = min(anchors) - pad
        resolve_window(fetch_start, end, self.cfg.max_history_days + pad.days + 2, default_days=0)

        fetch = secs + ([bench] if bench and bench not in secs else [])
        req = ops.build_historical(fetch, [fld], fetch_start, end, "DAILY", True, adjust_dividends, currency)
        results = self._cached(
            TTLCache.key(*req),
            self.cfg.cache_ttl_history,
            len(fetch),
            lambda: ops.parse_historical(self.backend.request(*req)),
        )

        # Build the series to measure: raw, inverted (1/x) or divided by the benchmark, day by day.
        by_sec = {e["security"]: e for e in results}
        notes: list[str] = []
        bench_obs = None
        if bench:
            bench_obs = changes.observations(by_sec.get(bench, {"rows": []})["rows"], fld)
            notes.extend(f"{bench}: {i.strip()}" for i in _issues(by_sec.get(bench, {})))
        series: list[tuple[str, list]] = []
        for sec in secs:
            entry = by_sec.get(sec, {"rows": []})
            notes.extend(f"{sec}: {i.strip()}" for i in _issues(entry))
            obs = changes.observations(entry["rows"], fld)
            label = sec
            if invert:
                obs, label = changes.invert(obs), f"1/({label})"
            if bench_obs is not None:
                obs, label = changes.ratio(obs, bench_obs), f"{label} / {bench}"
            series.append((label, obs))

        unit = "bps" if change_in_bps else "chg"
        scale = 100.0 if change_in_bps else 1.0
        desc = [f"{fld}"]
        if currency:
            desc.append(f"in {currency.upper()}")
        if invert:
            desc.append("inverted (1/x)")
        if bench:
            desc.append(f"divided by {bench} on each date")
        header = (
            f"{' '.join(desc)}; {_adjustment_label(True, adjust_dividends)}. All arithmetic is done by the server: "
            f"pct = (end / start - 1) x 100; {unit} = (end - start)"
            + (" x 100 (basis points, for yields quoted in %)" if change_in_bps else "")
            + ". Quote these numbers rather than recalculating."
        )

        if not plist:
            out = [
                header,
                f"Range {start}..{end}, measured from the close on or before each date.",
                f"security,start_date,start,end_date,end,ratio_end_over_start,{unit},pct_change,high,low",
            ]
            for label, obs in series:
                a = changes.value_on_or_before(obs, start)
                b = changes.value_on_or_before(obs, end)
                if a is None or b is None or a[0] >= b[0]:
                    out.append(_fmt(label) + "," * 9)
                    notes.append(f"{label}: not enough data in range")
                    continue
                window = [v for d, v in obs if a[0] <= d <= b[0]]
                abs_chg, pct = changes.change(a[1], b[1])
                out.append(",".join(_fmt(x) for x in (
                    label, a[0].isoformat(), a[1], b[0].isoformat(), b[1],
                    round(b[1] / a[1], 6) if a[1] else None, abs_chg * scale,
                    None if pct is None else round(pct, 3), max(window), min(window),
                )))
        else:
            out = [
                header,
                ",".join(["security", "date", "last"] + [f"{p}_{k}" for p in plist for k in ("pct", unit)]),
            ]
            for label, obs in series:
                pc = changes.period_changes(obs, plist)
                if not pc:
                    out.append(_fmt(label) + "," * (2 + 2 * len(plist)))
                    notes.append(f"{label}: no data")
                    continue
                cells = [label, pc["end_date"].isoformat(), pc["end"]]
                for p in plist:
                    r = pc["periods"][p]
                    cells += [None, None] if r is None else [None if r[3] is None else round(r[3], 3), r[2] * scale]
                out.append(",".join(_fmt(c) for c in cells))
        return "\n".join(out + ([""] + notes if notes else []))

    # --- reference ------------------------------------------------------

    def reference(self, securities: list[str] | str, fields: list[str] | str) -> str:
        secs = normalize_list(securities, "security", self.cfg.max_securities)
        flds = normalize_fields(fields, self.cfg.max_fields)
        req = ops.build_reference(secs, flds)
        results = self._cached(
            TTLCache.key(*req),
            self.cfg.cache_ttl_reference,
            len(secs) * len(flds),
            lambda: ops.parse_reference(self.backend.request(*req)),
        )
        out = [",".join(["security", *flds])]
        notes = []
        for entry in results:
            out.append(",".join([_fmt(entry["security"])] + [_fmt(entry["data"].get(f)) for f in flds]))
            if issues := _issues(entry):
                notes.append(entry["security"])
                notes.extend(issues)
        return "\n".join(out + ([""] + notes if notes else []))

    # --- intraday -------------------------------------------------------

    def intraday(
        self,
        security: str,
        interval_minutes: int = 60,
        start_datetime: str | None = None,
        end_datetime: str | None = None,
        event_type: str = "TRADE",
    ) -> str:
        sec = normalize_list(security, "security", 1)[0]
        if not 1 <= interval_minutes <= 1440:
            raise GuardError("interval_minutes must be between 1 and 1440.")
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
            end = datetime.fromisoformat(end_datetime) if end_datetime else now
            start = datetime.fromisoformat(start_datetime) if start_datetime else end - timedelta(days=5)
        except ValueError as exc:
            raise GuardError(f"Datetimes must be ISO format (YYYY-MM-DDTHH:MM, UTC): {exc}") from exc
        if start >= end:
            raise GuardError("start_datetime must be before end_datetime.")
        if (end - start).days > self.cfg.max_intraday_days:
            raise GuardError(f"Intraday window is limited to {self.cfg.max_intraday_days} days (Bloomberg's retention).")
        if (end - start) / timedelta(minutes=interval_minutes) > 5000:
            raise GuardError("That would return more than 5000 bars; use a larger interval or a shorter window.")

        req = ops.build_intraday(sec, interval_minutes, start, end, event_type.upper())
        bars = self._cached(
            TTLCache.key(*req), self.cfg.cache_ttl_reference, 1, lambda: ops.parse_intraday(self.backend.request(*req))
        )
        cols = ["time", "open", "high", "low", "close", "volume", "numEvents"]
        out = [f"## {sec} {interval_minutes}m {event_type.upper()} bars, {start}..{end} UTC ({len(bars)} bars)", ",".join(cols)]
        out.extend(",".join(_fmt(b.get(c)) for c in cols) for b in bars)
        return "\n".join(out)

    # --- lookups --------------------------------------------------------

    def search_securities(self, query: str, yellow_key: str | None = None, max_results: int = 10) -> str:
        if not query.strip():
            raise GuardError("query is required.")
        try:
            req = ops.build_security_search(query.strip(), yellow_key, max(1, min(max_results, 50)))
        except BloombergError as exc:
            raise GuardError(str(exc)) from exc
        results = self._cached(
            TTLCache.key(*req), 24 * 3600, 0, lambda: ops.parse_security_search(self.backend.request(*req))
        )
        if not results:
            return f"No securities matched {query!r}."
        return "\n".join(f"{r['security']}\t{r['description']}" for r in results)

    def search_fields(self, query: str, max_results: int = 15) -> str:
        if not query.strip():
            raise GuardError("query is required.")
        req = ops.build_field_search(query.strip())
        limit = max(1, min(max_results, 50))
        results = self._cached(
            TTLCache.key(*req, limit), 24 * 3600, 0, lambda: ops.parse_field_search(self.backend.request(*req), limit)
        )
        if not results:
            return f"No fields matched {query!r}."
        return "\n".join(f"{r['mnemonic']}\t{r['description']}\t{r['datatype']}" for r in results)

    # --- news -----------------------------------------------------------

    def _require_news(self) -> None:
        if self.cfg.news_mode == "disabled":
            raise GuardError("News is disabled in this server's configuration (news_mode: disabled).")

    def news_topics(self) -> str:
        lines = [f"news_mode: {self.cfg.news_mode}", f"signal fields: {', '.join(self.cfg.news_signal_fields)}"]
        lines += [f"{t}: {', '.join(secs)}" for t, secs in self.cfg.news_topics.items()]
        return "\n".join(lines)

    def news_signals(self, topic: str) -> str:
        self._require_news()
        basket = self.cfg.news_topics.get(topic.lower())
        if basket is None:
            raise GuardError(f"Unknown topic {topic!r}; available: {', '.join(self.cfg.news_topics)}.")
        return self._signals(basket, f"News activity for topic '{topic.lower()}'")

    def security_news_signals(self, securities: list[str] | str) -> str:
        self._require_news()
        secs = normalize_list(securities, "security", self.cfg.max_securities)
        return self._signals(secs, "News activity")

    def _signals(self, securities: list[str], title: str) -> str:
        flds = self.cfg.news_signal_fields
        req = ops.build_reference(securities, flds)
        results = self._cached(
            TTLCache.key(*req),
            self.cfg.cache_ttl_news,
            len(securities) * len(flds),
            lambda: ops.parse_reference(self.backend.request(*req)),
        )
        rank_field = flds[0]

        def score(entry: dict[str, Any]) -> float:
            v = entry["data"].get(rank_field)
            return float(v) if isinstance(v, (int, float)) else float("-inf")

        ranked = sorted(results, key=score, reverse=True)
        out = [
            f"{title}, ranked by {rank_field} (highest first).",
            "These are Bloomberg news heat/sentiment signals, not headlines. Open the Terminal "
            "(e.g. TOP <GO>, or <ticker> CN <GO>) to read the stories behind them.",
            ",".join(["security", *flds]),
        ]
        notes = []
        for entry in ranked:
            out.append(",".join([_fmt(entry["security"])] + [_fmt(entry["data"].get(f)) for f in flds]))
            if issues := _issues(entry):
                notes.append(entry["security"])
                notes.extend(issues)
        return "\n".join(out + ([""] + notes if notes else []))
