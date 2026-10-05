"""Offline watercolor composition and accessible mail/dashboard garden views."""
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
from math import ceil

from PIL import Image, ImageDraw, ImageFont

from stockwatch.garden import Garden, GardenEvent, SPECIES, STAGES
from stockwatch.i18n import t
from stockwatch.presentation import BACKGROUND, BORDER, HEADING_FONT, INK, NEUTRAL, InlineImage, atomic


@lru_cache(maxsize=3)
def _atlas(name):
    raw = files("stockwatch").joinpath("assets", "garden", name).read_bytes()
    with Image.open(BytesIO(raw)) as image:
        return image.convert("RGBA")


def _plant_sprite(species, stage):
    # Registered against the generated atlas, not guessed from CSS cell sizes.
    flowering = species >= 6
    image = _atlas("flowers.png" if flowering else "plants.png")
    species %= 6
    xs = (0, 200, 400, 600, 800, 1024)
    ys = (0, 250, 493, 735, 1007, 1267, 1536) if flowering else (0, 267, 486, 731, 989, 1227, 1536)
    if image.size != (1024, 1536):
        raise ValueError("Unexpected botanical atlas size")
    return image.crop((xs[stage], ys[species], xs[stage + 1], ys[species + 1]))


def _ornament(index):
    image = _atlas("ornaments.png")
    width, height = image.size
    x, y = index % 4, index // 4
    return image.crop((x * width // 4, y * height // 2,
                       (x + 1) * width // 4, (y + 1) * height // 2))


def _place(canvas, sprite, x, bottom, width):
    box = sprite.getbbox()
    if box is None:
        raise ValueError("Empty botanical sprite")
    # Scale from the original canvas width so young plants remain visibly smaller.
    scale = width / sprite.width
    crop = sprite.crop(box)
    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(crop, (round(x - crop.width / 2), round(bottom - crop.height)))


def compose_garden(garden: Garden) -> bytes:
    rich = garden.mode in ("WEEKLY", "MONTHLY")
    width, columns = 1200, 6
    rows = max(1, ceil(len(garden.plants) / columns))
    row_height = 240 if rich else 205
    height = 105 + rows * row_height + (70 if rich else 35)
    canvas = Image.new("RGBA", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    # Warm/cool wash changes only atmosphere; plant pixels stay healthy and unchanged.
    top = {"Warm light": (251, 242, 215), "Gentle rain": (232, 240, 241),
           "Soft clouds": (242, 239, 231), "Unknown": (240, 238, 232)}[garden.weather]
    bottom = (249, 244, 234)
    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line((0, y, width, y), fill=tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom)))
    font = ImageFont.load_default(size=23)
    small = ImageFont.load_default(size=18)
    draw.text((35, 28), garden.as_of.isoformat(), fill=NEUTRAL, font=small)
    weather_icon = {"Warm light": 0, "Gentle rain": 1, "Soft clouds": 2, "Unknown": 2}[garden.weather]
    _place(canvas, _ornament(weather_icon), width - 70, 103, 100)
    # Quarter-dependent foliage ornament, a decorative season rather than local weather.
    season_icon = {"Spring": 3, "Summer": 0, "Autumn": 7, "Winter": 2}[garden.season]
    _place(canvas, _ornament(season_icon), 60, height - 12, 65)
    if not garden.plants:
        draw.line((170, height - 70, width - 170, height - 70), fill=BORDER, width=2)
        _place(canvas, _ornament(6), width / 2, height - 80, 105)
    for index, plant in enumerate(garden.plants):
        row, column = divmod(index, columns)
        count = min(columns, len(garden.plants) - row * columns)
        left = (width - count * 180) / 2
        x = left + column * 180 + 90
        baseline = 90 + (row + 1) * row_height - 55
        draw.line((x - 75, baseline + 8, x + 75, baseline + 8), fill=BORDER, width=2)
        _place(canvas, _plant_sprite(plant.species, plant.stage), x, baseline, 155 if rich else 135)
        if garden.high_dates:
            _place(canvas, _ornament(3), x + 50, baseline + 5, 42)
        kinds = {event.kind for event in garden.events if event.symbol == plant.symbol}
        for offset, kind, icon in ((-55, "water", 4), (60, "prune", 5)):
            if kind in kinds:
                _place(canvas, _ornament(icon), x + offset, baseline + 8, 45)
        label_width = draw.textlength(plant.symbol, font=font)
        draw.text((x - label_width / 2, baseline + 17), plant.symbol, fill=INK, font=font)
        if "new" in kinds:
            since = plant.since.isoformat()
            label_width = draw.textlength(since, font=small)
            draw.text((x - label_width / 2, baseline + 45), since, fill=NEUTRAL, font=small)
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def event_line(event: GardenEvent, lang) -> str:
    names = {"new": "New seedling", "water": "Watering · added to holding",
             "prune": "Pruning · reduced holding", "archive": "Pressed leaf · closed holding after {days} days",
             "growth": "Grew to {stage} · {days} days held", "high": "Flowers · confirmed closing NAV high"}
    description = t(names[event.kind], lang, days=event.days, stage=t(STAGES[event.stage], lang))
    return f"{event.day.isoformat()} · " + (f"{event.symbol} · " if event.symbol else "") + description


@dataclass(frozen=True)
class GardenView:
    text: str
    html: str
    images: tuple[InlineImage, ...]


def garden_view(garden: Garden | None, lang: str) -> GardenView:
    if garden is None:
        return GardenView("", "", ())
    title = t("Holding garden", lang)
    heading = t("Garden through {date}", lang, date=garden.as_of.isoformat())
    atmosphere = f"{t(garden.season, lang)} · {t(garden.weather, lang)}"
    notice = t("Seasonal illustration, not actual weather. Plants represent holding time, not investment quality.", lang)
    extra = []
    if garden.mode == "INTRADAY":
        extra.append(t("Intraday atmosphere is an estimate; no closing high is celebrated.", lang))
    if garden.weather == "Unknown":
        extra.append(t("Market change unavailable; neutral scenery.", lang))
    plants = [t("{symbol} · {plant} · {days} days held · {stage}", lang, symbol=p.symbol,
                plant=t(SPECIES[p.species], lang), days=p.days, stage=t(STAGES[p.stage], lang))
              + " · " + t("Since {date}", lang, date=p.since.isoformat()) for p in garden.plants]
    if not plants:
        plants = [t("No current holdings; the garden is empty.", lang)]
    rich = garden.mode in ("WEEKLY", "MONTHLY")
    events_title = t("This week's garden" if garden.mode == "WEEKLY" else "This month's garden" if rich else "Today's garden", lang)
    current_events = [event_line(event, lang) for event in garden.events if event.kind != "archive"]
    archives = [event_line(event, lang) for event in garden.events if event.kind == "archive"]
    if not rich:
        current_events += archives
        archives = []
    if not current_events:
        current_events = [t("No garden events in this period." if rich else "No recorded trades today.", lang)]
    image_html, images = "", ()
    try:
        png = compose_garden(garden)
        image = InlineImage(f"garden-{sha256(png).hexdigest()[:16]}@stockwatch.invalid", png)
        images = (image,)
        image_html = f"<img src='{image.data_url}' alt='{escape(title)}' width='600' style='display:block;width:100%;max-width:600px;height:auto;border-radius:8px;margin:12px 0'>"
    except (OSError, ValueError):
        extra.append(t("Botanical illustration unavailable; the written garden remains below.", lang))
    def paragraphs(items):
        return "".join(f"<p style='margin:6px 0;font-size:12px;line-height:1.75;overflow-wrap:break-word;text-wrap:pretty'>{escape(line)}</p>" for line in items)
    plant_rows = []
    for plant in garden.plants:
        label = atomic(plant.symbol) + f" <span style='font-size:12px;color:{NEUTRAL}'>· {escape(t(SPECIES[plant.species], lang))}</span>"
        plant_rows.append(
            f"<tr><td style='padding:11px 8px 4px 0;font-size:14px;font-weight:600;vertical-align:top'>{label}</td>"
            f"<td style='padding:11px 0 4px;text-align:right;font-size:12px;color:{NEUTRAL};vertical-align:top'>{atomic(t(STAGES[plant.stage], lang))}</td></tr>"
            f"<tr><td style='padding:0 8px 11px 0;font-size:11px;color:{NEUTRAL};border-bottom:1px solid {BORDER}'>{atomic(t('{days} days held', lang, days=plant.days))}</td>"
            f"<td style='padding:0 0 11px;text-align:right;font-size:11px;color:{NEUTRAL};border-bottom:1px solid {BORDER}'>{atomic(t('Since {date}', lang, date=plant.since.isoformat()))}</td></tr>")
    plants_html = ("<table class='garden-plants' role='presentation' style='width:100%;border-collapse:collapse'>"
                   + "".join(plant_rows) + "</table>") if plant_rows else paragraphs(plants)

    def events_html(events):
        rendered = []
        high_dates = [event.day for event in events if rich and event.kind == "high"]
        for event in events:
            if rich and event.kind == "high":
                continue
            labels = {"new": "New seedling", "water": "Added to holding", "prune": "Reduced holding",
                      "archive": "Closed holding", "high": "Closing NAV high"}
            label = t(STAGES[event.stage], lang) if event.kind == "growth" else t(labels[event.kind], lang)
            description = atomic(label)
            if event.kind in ("growth", "archive"):
                description += " · " + atomic(t("{days} days held", lang, days=event.days))
            name = event.symbol or label
            detail = (f"<tr><td colspan='2' style='padding:0 0 9px;font-size:12px;color:{NEUTRAL};text-wrap:pretty'>{description}</td></tr>"
                      if event.symbol else "")
            rendered.append(f"<tr aria-label='{escape(event_line(event, lang), quote=True)}'><td style='padding:8px 8px 3px 0;font-size:13px;vertical-align:top'>{atomic(name)}</td>"
                            f"<td style='padding:8px 0 3px;text-align:right;font-size:11px;color:{NEUTRAL};vertical-align:top'>{atomic(event.day.isoformat())}</td></tr>{detail}")
        highs = ""
        if high_dates:
            dates = "".join(f"<span style='display:inline-block;white-space:nowrap;margin:3px 4px 3px 0;padding:3px 6px;background:white;border-radius:4px;font-size:10px;color:{NEUTRAL}'>{day.isoformat()}</span>" for day in high_dates)
            highs = f"<div style='margin-top:10px'><div style='font-size:12px;margin-bottom:5px'>{escape(t('{count} confirmed closing NAV highs', lang, count=len(high_dates)))}</div>{dates}</div>"
        return "<table role='presentation' style='width:100%;border-collapse:collapse'>" + "".join(rendered) + "</table>" + highs
    visible_events = [event for event in garden.events if not rich or event.kind != "archive"]
    current_html = events_html(visible_events) if visible_events else paragraphs(current_events)
    body = (f"<section class='sw-garden' style='margin:20px 0;padding:18px;background:{BACKGROUND};border:1px solid {BORDER};border-radius:10px;color:{INK}'>"
            f"<h2 style='font-family:{HEADING_FONT};font-size:18px;margin:0'>{escape(title)}</h2>"
            f"<table role='presentation' style='width:100%;border-collapse:collapse;margin:8px 0 0;font-size:11px;color:{NEUTRAL}'><tr>"
            f"<td style='padding-right:8px'>{atomic(t('As of {date}', lang, date=garden.as_of.isoformat()))}</td>"
            f"<td style='text-align:right'>{atomic(atmosphere)}</td></tr></table>"
            + image_html + plants_html
            + f"<h3 style='font-size:14px;margin:18px 0 4px'>{escape(events_title)}</h3>" + current_html)
    lines = [title, heading, atmosphere, *plants, events_title, *current_events]
    if archives:
        archive_title = t("Past botanical notes", lang)
        lines += [archive_title, *archives]
        body += f"<h3 style='font-size:14px;margin:18px 0 4px'>{escape(archive_title)}</h3>" + events_html([event for event in garden.events if event.kind == "archive"])
    lines += [*extra, notice]
    short_notice = (("Seasonal illustration", "Not actual weather"), ("Holding time only", "Not investment quality"))
    body += paragraphs(extra) + f"<table role='presentation' style='width:100%;border-collapse:collapse;margin-top:12px;font-size:10px;color:{NEUTRAL}'>"
    body += "".join(f"<tr><td style='padding:3px 8px 3px 0'>{atomic(t(left, lang))}</td>"
                    f"<td style='padding:3px 0;text-align:right'>{atomic(t(right, lang))}</td></tr>" for left, right in short_notice) + "</table></section>"
    return GardenView("\n".join(lines), body, images)
