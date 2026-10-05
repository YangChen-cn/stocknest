"""Small, dependency-free visual vocabulary shared by the dashboard and mail."""
from base64 import b64encode
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape

from stockwatch.i18n import CLASSICS, t

BACKGROUND = "#f6f1e7"
INK = "#24352f"
GREEN = "#28634d"
RED = "#b55249"
NEUTRAL = "#68756e"
BORDER = "#e3e7e1"
SOFT = "#eef1e7"
PALETTE = [GREEN, "#568b76", "#a3c3b4", "#b59c76", "#8499a6", "#a798ad"]
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"
HEADING_FONT = "Georgia,Songti SC,Noto Serif CJK SC,serif"


def value_color(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) < Decimal('0.005'):
            return NEUTRAL
        return GREEN if number > 0 else RED
    except (InvalidOperation, ValueError):
        return NEUTRAL


@dataclass(frozen=True)
class InlineImage:
    cid: str
    data: bytes

    @property
    def data_url(self) -> str:
        return "data:image/png;base64," + b64encode(self.data).decode("ascii")


def classic(day: date):
    original_language = "zh-CN" if day.toordinal() % 2 else "en"
    pool = CLASSICS[original_language]
    return original_language, pool[(day.toordinal() // 2) % len(pool)]


def reflection(day: date, lang: str) -> str:
    original_language, (words, author, work, source, gloss) = classic(day)
    note = f"\n{t('Reading note', lang)}: {gloss}" if original_language != lang else ""
    return f"{words}\n— {author} · {work}{note}\n{t('Source', lang)}: {source}"


def reflection_html(day: date, lang: str) -> str:
    original_language, (words, author, work, source, gloss) = classic(day)
    note = (f"<p style='font-size:12px;color:{NEUTRAL};margin:10px 0 0'>{escape(t('Reading note', lang))} · {escape(gloss)}</p>"
            if original_language != lang else "")
    verse = escape(words).replace("\n", "<br>")
    return (f"<div class='sw-classic' style='padding:20px 18px;background:{BACKGROUND};border-radius:8px'>"
            f"<p lang='{original_language}' style='font-family:{HEADING_FONT};font-size:18px;line-height:1.8;margin:0;color:{INK}'>{verse}</p>"
            f"<p style='font-size:12px;color:{NEUTRAL};margin:10px 0 0'>— {escape(author)} · {escape(work)} · "
            f"<a href='{escape(source, quote=True)}' style='color:{GREEN}'>{escape(t('Source', lang))}</a></p>{note}</div>")


def record_note(history, lang: str) -> str | None:
    points = history.get('points', []) if history else []
    # The first point is the pre-contribution baseline, not a day of performance.
    count = sum(p.get('nav') is not None for p in points[1:])
    return t('History covers {count} recorded trading days.', lang, count=count) if count else None


def email_section(title: str, content: str) -> str:
    return (f"<section style='margin-top:24px;border-top:1px solid {BORDER};padding-top:16px'>"
            f"<h2 style='font-family:{HEADING_FONT};font-size:20px;color:{INK};margin:0 0 12px'>{escape(title)}</h2>{content}</section>")


def email_shell(title: str, subtitle: str, content: str, lang: str, *, closing: str | None = None,
                closing_html: str | None = None) -> str:
    footer = (f"<div style='margin-top:24px'>{closing_html or escape(closing)}</div>") if closing else ''
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>@media(max-width:480px){{.mail-body{{padding:20px 14px!important}}.mail-table td,.mail-table th{{padding:8px 4px!important}}.mail-table{{font-size:13px!important}}.mail-hero{{font-size:26px!important}}}}</style></head>
<body style="margin:0;padding:12px 4px;background:{BACKGROUND};font-family:{FONT};color:{INK};font-size:15px;line-height:1.6;font-variant-numeric:tabular-nums">
<table role="presentation" style="width:100%;border-collapse:collapse"><tr><td align="center">
<div class="mail-body" style="max-width:640px;margin:auto;background:#fff;padding:28px 26px;text-align:left">
<h1 style="font-family:{HEADING_FONT};font-size:27px;line-height:1.3;margin:0 0 8px;color:{INK}">{escape(title)}</h1>
<p style="margin:0 0 24px;font-size:14px;color:{NEUTRAL}">{escape(subtitle)}</p>
{content}{footer}</div></td></tr></table></body></html>'''
