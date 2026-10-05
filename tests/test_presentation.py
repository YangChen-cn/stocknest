from datetime import date, datetime, timezone
from decimal import Decimal
import json

import pytest

from stockwatch.presentation import GREEN, RED, NEUTRAL, record_note, value_color
from stockwatch.portfolio import calculate
from stockwatch.reports import render_failure, render_report


@pytest.mark.parametrize('value,expected', [(None, NEUTRAL), (0, NEUTRAL), (Decimal('-0.001'), NEUTRAL),
                                           (float('nan'), NEUTRAL), (1, GREEN), (-1, RED)])
def test_signed_display_has_neutral_zero_and_missing(value, expected):
    assert value_color(value) == expected


def test_record_count_excludes_baseline_and_missing_days():
    assert record_note(None, 'en') is None
    assert record_note({'points': [{'nav': 100}]}, 'en') is None
    assert record_note({'points': [{'nav': 100}, {'nav': 101}, {'nav': None}]}, 'zh-CN') == '历史已覆盖 1 个有效交易日。'


def test_warm_copy_cannot_change_machine_data_or_error_status(monkeypatch):
    config = {'portfolio': {'language': 'en'}, 'watchlist': {}}
    args = (date(2026, 10, 6), calculate({}, {}), {}, config, [])
    now = datetime(2026, 10, 6, 23, tzinfo=timezone.utc)
    before = render_report(*args, generated_at=now)
    monkeypatch.setattr('stockwatch.presentation.CLASSICS', {
        lang: (('A quieter closing line.', 'Test author', 'Test work', 'https://example.invalid/source', 'Test gloss'),)
        for lang in ('en', 'zh-CN')})
    after = render_report(*args, generated_at=now)
    assert before.data_json == after.data_json
    assert after.html.count('A quieter closing line.') == 1
    failure = render_failure(args[0], 'Close data remains unavailable; the daily report was not sent.', ['DEMO'], 3, 'en')
    assert 'A quieter closing line.' not in failure.html
    assert json.loads(failure.data_json)['price_alerts_consumed'] is False
