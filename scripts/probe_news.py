"""Phase 0 probe: what news data does this Terminal login expose via DAPI?

Run on the Terminal machine:  python scripts/probe_news.py [report.txt]

Checks, without spending meaningful data hits:
  1. which candidate news-related services open for this login;
  2. which field mnemonics match news-related searches (via //blp/apiflds);
  3. whether the configured news signal fields return values for SPX Index.
"""

from __future__ import annotations

import sys

from bbg_mcp import ops
from bbg_mcp.backends.base import BloombergError
from bbg_mcp.backends.blpapi_backend import BlpapiBackend
from bbg_mcp.config import load_config

# Service names to try. Most are expected to fail on a plain Terminal login;
# a success is the signal that headline retrieval may be possible.
CANDIDATE_SERVICES = ["//blp/refdata", "//blp/apiflds", "//blp/instruments", "//blp/bqlsvc", "//blp/news", "//blp/mktnews"]
FIELD_QUERIES = ["news", "headline", "news heat", "news sentiment", "story"]


def main() -> None:
    cfg = load_config()
    backend = BlpapiBackend(cfg.host, cfg.port, cfg.timeout_seconds)
    out: list[str] = []
    emit = out.append

    emit(f"status: {backend.status()}")
    emit("\n# Services")
    for svc in CANDIDATE_SERVICES:
        emit(f"{svc}: {'OPEN' if backend.can_open(svc) else 'unavailable'}")

    emit("\n# News-related fields")
    seen: set[str] = set()
    for q in FIELD_QUERIES:
        try:
            fields = ops.parse_field_search(backend.request(*ops.build_field_search(q)), limit=40)
        except BloombergError as exc:
            emit(f"[{q}] error: {exc}")
            continue
        for f in fields:
            if f["mnemonic"] not in seen:
                seen.add(f["mnemonic"])
                emit(f"{f['mnemonic']}\t{f['description']}\t{f['datatype']}")

    emit("\n# Configured signal fields on SPX Index")
    try:
        [entry] = ops.parse_reference(backend.request(*ops.build_reference(["SPX Index"], cfg.news_signal_fields)))
        for f in cfg.news_signal_fields:
            emit(f"{f}: {entry['data'].get(f, '(no value)')}")
        for fe in entry["field_exceptions"]:
            emit(f"FIELD ERROR {fe}")
    except BloombergError as exc:
        emit(f"error: {exc}")

    backend.close()
    report = "\n".join(out)
    print(report)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write(report)


if __name__ == "__main__":
    main()
