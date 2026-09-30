import json

import pytest

from stockwatch.storage import (ValidationError, atomic_write, load_config, load_state, load_transactions,
                                save_config, save_state, save_transactions, validate_config)


def test_csv_roundtrip_preserves_precision_and_quoted_notes(tmp_path):
    path = tmp_path / "tx.csv"
    save_transactions(path, [{"date": "2026-09-28", "symbol": "abc", "side": "buy", "shares": "0.123456789123456789",
                              "price": "12.3456789123456789", "note": "comma, and\nnewline"}])
    row = load_transactions(path)[0]
    assert row.symbol == "ABC" and str(row.shares) == "0.123456789123456789"
    assert row.note == "comma, and\nnewline"


@pytest.mark.parametrize("content", ["symbol,shares\nXYZ,1\n", "date,symbol,side,shares,price,note\n2026-09-28,XYZ,BUY,1\n",
                                     "date,symbol,side,shares,price,note\n2026-09-28,XYZ,BUY,1,10,note,extra\n"])
def test_invalid_csv_shapes(tmp_path, content):
    path = tmp_path / "tx.csv"
    path.write_text(content)
    with pytest.raises(ValidationError):
        load_transactions(path)


@pytest.mark.parametrize("raw", [None, {"password": "secret"}, {"portfolio": {"base_currency": "HKD"}},
                                 {"watchlist": {"X": {"alerts": {"below": -1}}}},
                                 {"watchlist": {"X": {"alerts": {"daily_move_pct": "NaN"}}}},
                                 {"watchlist": {"X": {"alerts": {"below": "1e999"}}}},
                                 {"watchlist": {"X": {"alerts": {"below": "1e-999"}}}},
                                 {"watchlist": {"X": {"password": "secret"}}},
                                 {"watchlist": {"x": {}, "X": {}}}])
def test_invalid_configs(raw):
    with pytest.raises(ValidationError):
        validate_config(raw)


def test_config_roundtrip_and_invalid_save_preserves_original(tmp_path):
    path = tmp_path / "config.yaml"
    config = {"portfolio": {"base_currency": "USD"}, "watchlist": {"abc": {"thesis": "Some facts", "target_shares": 0}}}
    save_config(path, config)
    assert "ABC" in load_config(path)["watchlist"]
    original = path.read_bytes()
    with pytest.raises(ValidationError):
        save_config(path, {"password": "never-save"})
    assert path.read_bytes() == original


def test_atomic_failure_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "file"
    path.write_text("original")
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr("stockwatch.storage.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write(path, "replacement")
    assert path.read_text() == "original" and not list(tmp_path.glob("*.tmp"))


def test_state_roundtrip_unchanged_and_corrupt(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path) == {}
    state = {"ABC": {"below_50.5": {"triggered": True, "last_notified": "2026-09-30"}},
             "_meta": {"last_report_session": "2026-09-30"}}
    save_state(path, state)
    modified = path.stat().st_mtime_ns
    save_state(path, state)
    assert path.stat().st_mtime_ns == modified and load_state(path) == state
    path.write_text("{broken")
    with pytest.raises(ValidationError):
        load_state(path)
    path.write_text(json.dumps({"ABC": {"below_1": {"triggered": "true"}}}))
    with pytest.raises(ValidationError):
        load_state(path)


def test_first_run_creates_empty_files_without_replacing_existing(tmp_path):
    from stockwatch.storage import ensure_user_files
    (tmp_path / "config.example.yaml").write_text("portfolio:\n  language: en\nwatchlist: {}\n")
    ensure_user_files(tmp_path)
    assert load_transactions(tmp_path / "data/transactions.csv") == []
    assert load_config(tmp_path / "config.yaml")["watchlist"] == {}
    original = (tmp_path / "config.yaml").read_bytes()
    ensure_user_files(tmp_path)
    assert (tmp_path / "config.yaml").read_bytes() == original
