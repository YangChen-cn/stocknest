import json
import subprocess
from unittest.mock import Mock

import pytest

from stockwatch.providers.base import DataUnavailable
from stockwatch.providers.openbb_provider import OpenBBProvider, normalize_search


def test_only_us_stocks_etfs_with_names_and_deduplication():
    rows = [
        {"symbol": "XYZ", "longname": "Example & Co", "exchange": "NMS", "quoteType": "EQUITY"},
        {"symbol": "FUND", "shortname": "Example ETF", "exchange": "PCX", "quoteType": "ETF"},
        {"symbol": "XYZ", "exchange": "NMS", "quoteType": "EQUITY"},
        {"symbol": "XYZ.TO", "exchange": "TOR", "quoteType": "EQUITY"},
        {"symbol": "XYZOPTION", "exchange": "OPR", "quoteType": "OPTION"},
        {"symbol": "BTC-USD", "exchange": "CCC", "quoteType": "CRYPTOCURRENCY"},
        {"symbol": "", "exchange": "NMS", "quoteType": "EQUITY"},
        None, {"symbol": None}, {"symbol": "INVALID", "exchange": [], "quoteType": []},
    ]
    results = normalize_search(rows)
    assert [item.symbol for item in results] == ["XYZ", "FUND"]
    assert results[0].name == "Example & Co" and results[1].kind == "ETF"


def test_search_dispatch_alias_and_bounded_worker(monkeypatch):
    worker = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({"data": [
        {"symbol": "DYNAMIC", "longname": "Dynamic result", "exchange": "NYQ", "quoteType": "EQUITY"}]}), ""))
    monkeypatch.setattr("stockwatch.providers.openbb_provider.subprocess.run", worker)
    results = OpenBBProvider().search(" 苹果 ")
    assert results[0].symbol == "DYNAMIC"
    assert json.loads(worker.call_args.kwargs["input"]) == {"operation": "search", "query": "Apple"}
    assert worker.call_args.kwargs["timeout"] <= 20


@pytest.mark.parametrize("output", ['{"error":"NetworkError"}', "bad", '{"data":null}'])
def test_search_failure_is_safe(monkeypatch, output):
    monkeypatch.setattr("stockwatch.providers.openbb_provider.subprocess.run", Mock(return_value=subprocess.CompletedProcess([], 0, output, "")))
    with pytest.raises(DataUnavailable, match="Search unavailable"):
        OpenBBProvider().search("Something")


def test_search_timeout_empty_query(monkeypatch):
    worker = Mock(side_effect=subprocess.TimeoutExpired("worker", 20))
    monkeypatch.setattr("stockwatch.providers.openbb_provider.subprocess.run", worker)
    assert OpenBBProvider().search(" ") == [] and not worker.called
    with pytest.raises(DataUnavailable, match="Search unavailable"):
        OpenBBProvider().search("Example")


def test_offline_demo_search_uses_fixture_names():
    from stockwatch.providers.demo import DemoProvider
    assert DemoProvider().search(" ") == []
    assert DemoProvider().search("core")[0].kind == "ETF"
    assert DemoProvider().search("value")[0].name == "Synthetic Value Company"
