from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from stockwatch.garden import SPECIES, build_garden
from stockwatch.garden_render import garden_view
from stockwatch.i18n import CLASSICS
from stockwatch.notifications.email import EmailSettings, make_message
from stockwatch.performance import fingerprint
from stockwatch.portfolio import calculate, positions
from stockwatch.presentation import classic, reflection
from stockwatch.providers.base import Quote
from stockwatch.reports import render_failure, render_report
from stockwatch.storage import Transaction

DAY = date(2026, 10, 6)
CONFIG = {"portfolio": {"language": "zh-CN", "benchmark": "SPYM"}, "watchlist": {}}


def tx(day, symbol="SYNTH", side="BUY", shares="1"):
    return Transaction(day, symbol, side, Decimal(shares), Decimal("10"))


def book(rows, value=11, prev=10):
    held = positions(rows, DAY)
    return calculate(held, {symbol: Quote(symbol, value, prev, session=DAY) for symbol in held})


def history(rows):
    return {"fingerprint": fingerprint(rows, "SPYM"), "benchmark": "SPYM",
            "points": [{"date": d, "nav": nav, "benchmark_nav": 100}
                       for d, nav in zip(("2026-10-02", "2026-10-05", "2026-10-06"), (100, 110, 121))]}


@pytest.mark.parametrize("days,stage", [(1, 0), (6, 0), (7, 1), (29, 1), (30, 2), (89, 2),
                                       (90, 3), (179, 3), (180, 4), (365, 4)])
def test_holding_age_and_stage_boundaries(days, stage):
    start = DAY - timedelta(days=days - 1)
    rows = [tx(start)]
    garden = build_garden(rows, book(rows), DAY)
    assert (garden.plants[0].days, garden.plants[0].stage, garden.plants[0].since) == (days, stage, start)


def test_new_water_prune_archive_reentry_and_same_day_order():
    rows = [tx(date(2026, 9, 28), shares="3"), tx(DAY, shares="2"),
            tx(DAY, side="SELL", shares="1"), tx(DAY, side="SELL", shares="4"), tx(DAY),
            tx(DAY + timedelta(days=1), "FUTURE")]
    original = deepcopy(rows)
    garden = build_garden(rows, book(rows), DAY)
    assert [event.kind for event in garden.events] == ["water", "prune", "archive", "new"]
    assert garden.events[2].days == 9
    assert garden.plants[0].days == 1 and garden.plants[0].since == DAY
    assert len(garden.plants) == 1 and rows == original


def test_added_shares_do_not_reset_age_and_order_species_are_stable():
    rows = [tx(date(2026, 9, 28), "ZZZ"), tx(date(2026, 9, 28), "AAA"), tx(DAY, "AAA")]
    garden = build_garden(rows, book(rows), DAY)
    assert [plant.symbol for plant in garden.plants] == ["AAA", "ZZZ"]
    assert all(plant.days == 9 for plant in garden.plants)
    other = build_garden([rows[1], rows[0], rows[2]], book(rows), DAY)
    assert garden.plants == other.plants
    assert len(SPECIES) == 12


@pytest.mark.parametrize("value,weather", [(1, "Warm light"), (-1, "Gentle rain"), (0, "Soft clouds"),
                                          (Decimal("0.004"), "Soft clouds"), (None, "Unknown"), (float("nan"), "Unknown")])
def test_weather_is_display_only_and_missing_stays_neutral(value, weather):
    rows = [tx(DAY)]
    portfolio = book(rows)
    portfolio["daily_pct"] = value
    garden = build_garden(rows, portfolio, DAY)
    assert garden.weather == weather and garden.plants[0].stage == 0


def test_only_valid_completed_fingerprinted_history_blooms():
    rows = [tx(date(2026, 10, 5))]
    portfolio = book(rows)
    valid = history(rows)
    assert build_garden(rows, portfolio, DAY, history=valid).high_dates == (DAY,)
    assert not build_garden(rows, portfolio, DAY, history=valid, mode="INTRADAY").high_dates
    for mutation in ("fingerprint", "stale", "missing", "gap", "future"):
        invalid = deepcopy(valid)
        if mutation == "fingerprint": invalid["fingerprint"] = "different-ledger"
        if mutation == "stale": invalid["points"].pop()
        if mutation == "missing": invalid["points"][1]["nav"] = None
        if mutation == "gap": invalid["points"].pop(1)
        if mutation == "future": invalid["points"].append({"date": "2026-10-07", "nav": 130})
        assert not build_garden(rows, portfolio, DAY, history=invalid).high_dates
    assert not build_garden(rows, {**portfolio, "complete": False}, DAY, history=valid).high_dates


@pytest.mark.parametrize("mode", ["WEEKLY", "MONTHLY"])
def test_period_growth_archive_weather_highs_and_boundaries(mode):
    rows = [tx(date(2026, 9, 29), "CLOSED"), tx(date(2026, 10, 5), "CLOSED", "SELL"),
            tx(date(2026, 10, 5), "CURRENT")]
    garden = build_garden(rows, book(rows), DAY, mode=mode, period_start=date(2026, 10, 1),
                          period_return=Decimal("-2"), history=history(rows))
    assert garden.weather == "Gentle rain" and garden.high_dates == (date(2026, 10, 5), DAY)
    assert [plant.symbol for plant in garden.plants] == ["CURRENT"]
    assert any(event.kind == "growth" and event.symbol == "CLOSED" and event.day == date(2026, 10, 5) for event in garden.events)
    view = garden_view(garden, "zh-CN")
    assert "往期花笺" in view.text and "连续持有 7 天后清仓" in view.text
    assert "2026-09-29 · CLOSED · 添新苗" not in view.text
    assert "2026-10-05 · CLOSED" in view.html
    unavailable = build_garden(rows, book(rows), DAY, mode=mode, period_return=None)
    assert unavailable.weather == "Unknown"  # never use positive daily change as a period return


def test_render_images_text_cid_json_and_fallback(monkeypatch):
    rows = [tx(date(2026, 10, 5))]
    portfolio = book(rows)
    garden = build_garden(rows, portfolio, DAY)
    view = garden_view(garden, "zh-CN")
    assert "连续持有 2 天" in view.text and "data:image/png;base64," in view.html
    with Image.open(BytesIO(view.images[0].data)) as image:
        assert image.width == 1200 and image.height < 450
    # Fixed generation time is necessary for byte-identical machine data.
    now = datetime(2026, 10, 6, 22, tzinfo=timezone.utc)
    old = render_report(DAY, portfolio, {}, CONFIG, [], generated_at=now)
    new = render_report(DAY, portfolio, {}, CONFIG, [], generated_at=now, garden=garden)
    assert old.data_json == new.data_json and old.subject == new.subject
    message = make_message(new, EmailSettings("sender@example.invalid", "secret", "reader@example.invalid"))
    html = message.get_body(preferencelist=("html",)).get_content()
    assert f"cid:{new.inline_images[0].cid}" in html and "data:image/png" not in html
    inline = [part for part in message.walk() if part.get_content_disposition() == "inline"]
    assert len(inline) == 1 and inline[0]["Content-ID"] == f"<{new.inline_images[0].cid}>"
    assert inline[0].get_payload(decode=True) == view.images[0].data
    assert [part.get_content_type() for part in message.iter_attachments()] == ["application/json"]
    from email import policy
    from email.parser import BytesParser
    wire = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert f"cid:{new.inline_images[0].cid}" in wire.get_body(preferencelist=("html",)).get_content()
    assert next(wire.iter_attachments()).get_payload(decode=True).decode() == new.data_json
    from stockwatch import garden_render
    def broken(*args): raise OSError("asset unavailable")
    monkeypatch.setattr(garden_render, "_atlas", broken)
    fallback = garden_view(garden, "en")
    assert not fallback.images and "2 days held" in fallback.text
    assert "Botanical illustration unavailable" in fallback.html


def test_empty_garden_failure_and_escaping():
    empty = garden_view(build_garden([], calculate({}, {}), DAY), "en")
    assert "No current holdings" in empty.html and empty.images
    rows = [tx(DAY, "<script>")]
    view = garden_view(build_garden(rows, book(rows), DAY), "en")
    assert "<script>" not in view.html and "&lt;script&gt;" in view.html
    failure = render_failure(DAY, "Close data remains unavailable; the daily report was not sent.", [], 3, "zh-CN")
    assert "持仓花园" not in failure.html and "sw-classic" not in failure.html and not failure.inline_images


def test_all_classics_rotate_original_language_and_have_sources():
    seen = {"zh-CN": set(), "en": set()}
    for offset in range(24):
        day = DAY + timedelta(days=offset)
        lang, entry = classic(day)
        seen[lang].add(entry[0])
        assert entry[0] in reflection(day, "zh-CN") and entry[0] in reflection(day, "en")
        assert entry[1] and entry[2] and entry[3].startswith("https://") and entry[4]
        assert CLASSICS[lang][(day.toordinal() // 2) % 12] == entry
    assert len(seen["zh-CN"]) == len(seen["en"]) == 12
