"""End-to-end check against the configured backend (live Terminal by default).

    python scripts/smoke_test.py            # live
    BBG_MCP_BACKEND=mock python scripts/smoke_test.py
"""

from __future__ import annotations

import sys

from bbg_mcp.backends import make_backend
from bbg_mcp.config import load_config
from bbg_mcp.service import BloombergService

STEPS = [
    ("status", lambda s: s.status()),
    ("1y weekly history", lambda s: s.historical(["SPX Index", "AAPL US Equity"], ["PX_LAST"], periodicity="WEEKLY")),
    ("snapshot", lambda s: s.reference(["AAPL US Equity", "CO1 Comdty"], ["NAME", "PX_LAST"])),
    ("security search", lambda s: s.search_securities("brent crude", max_results=5)),
    ("field search", lambda s: s.search_fields("dividend yield", max_results=5)),
    ("news signals", lambda s: s.news_signals("energy")),
    ("cache check", lambda s: s.reference(["AAPL US Equity", "CO1 Comdty"], ["NAME", "PX_LAST"])),
]


def main() -> int:
    cfg = load_config()
    backend = make_backend(cfg)
    svc = BloombergService(cfg, backend)
    failures = 0
    for name, step in STEPS:
        print(f"\n===== {name} =====")
        try:
            text = step(svc)
            lines = text.splitlines()
            print("\n".join(lines[:12]) + (f"\n... ({len(lines) - 12} more lines)" if len(lines) > 12 else ""))
        except Exception as exc:  # noqa: BLE001 - report every step
            failures += 1
            print(f"FAILED: {exc}")
    print(f"\nhits used today (approx): {svc.budget.used_today()}")
    backend.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
