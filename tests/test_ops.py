from datetime import date

import pytest

from bbg_mcp import ops
from bbg_mcp.backends.base import BloombergError


def test_build_historical_params():
    service, op, p = ops.build_historical(
        ["SPX Index"], ["PX_LAST"], date(2025, 10, 22), date(2026, 10, 22), "WEEKLY", overrides={"eqy_fund_crncy": "EUR"}
    )
    assert (service, op) == ("//blp/refdata", "HistoricalDataRequest")
    assert p["startDate"] == "20251022" and p["endDate"] == "20261022"
    assert p["periodicitySelection"] == "WEEKLY"
    assert p["overrides"] == [{"fieldId": "EQY_FUND_CRNCY", "value": "EUR"}]


def test_parse_historical_merges_partial_messages_and_errors():
    msgs = [
        {"securityData": {"security": "A", "fieldExceptions": [], "fieldData": [{"date": date(2026, 1, 2), "PX_LAST": 1.0}]}},
        {"securityData": {"security": "A", "fieldExceptions": [], "fieldData": [{"date": date(2026, 1, 5), "PX_LAST": 2.0}]}},
        {"securityData": {"security": "B", "securityError": {"message": "Unknown security"}, "fieldData": []}},
    ]
    out = ops.parse_historical(msgs)
    assert out[0]["rows"] == [{"date": "2026-01-02", "PX_LAST": 1.0}, {"date": "2026-01-05", "PX_LAST": 2.0}]
    assert out[1]["error"] == "Unknown security"


def test_parse_reference_field_exceptions():
    msgs = [{"securityData": [{
        "security": "A",
        "fieldData": {"PX_LAST": 3.0},
        "fieldExceptions": [{"fieldId": "BAD", "errorInfo": {"message": "Field not valid"}}],
    }]}]
    [entry] = ops.parse_reference(msgs)
    assert entry["data"] == {"PX_LAST": 3.0}
    assert entry["field_exceptions"] == ["BAD: Field not valid"]


def test_response_error_raises():
    with pytest.raises(BloombergError, match="Not authorized"):
        ops.parse_reference([{"responseError": {"message": "Not authorized"}}])


@pytest.mark.parametrize(
    "raw,expected",
    [("AAPL US<equity>", "AAPL US Equity"), ("SPX<index>", "SPX Index"), ("CO1<cmdty>", "CO1 Comdty"), ("XYZ", "XYZ")],
)
def test_to_api_ticker(raw, expected):
    assert ops.to_api_ticker(raw) == expected


def test_security_search_rejects_unknown_yellow_key():
    with pytest.raises(BloombergError, match="yellow_key"):
        ops.build_security_search("x", "stocks", 5)
