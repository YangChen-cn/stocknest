"""Small, dependency-free visual vocabulary shared by the dashboard and mail."""
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape

from stockwatch.i18n import QUOTES, t

BACKGROUND = "#f7f6f2"
INK = "#24352f"
GREEN = "#28634d"
RED = "#b55249"
NEUTRAL = "#68756e"
BORDER = "#e3e7e1"
SOFT = "#eef3ee"
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


def reflection(day: date, lang: str) -> str:
    pair = QUOTES[day.toordinal() % len(QUOTES)]
    return pair[0 if lang == 'zh-CN' else 1]


def record_note(history, lang: str) -> str | None:
    points = history.get('points', []) if history else []
    # The first point is the pre-contribution baseline, not a day of performance.
    count = sum(p.get('nav') is not None for p in points[1:])
    return t('History covers {count} recorded trading days.', lang, count=count) if count else None


def email_section(title: str, content: str) -> str:
    return (f"<section style='margin-top:24px;border-top:1px solid {BORDER};padding-top:16px'>"
            f"<h2 style='font-family:{HEADING_FONT};font-size:20px;color:{INK};margin:0 0 12px'>{escape(title)}</h2>{content}</section>")


def email_shell(title: str, subtitle: str, content: str, lang: str, *, closing: str | None = None) -> str:
    footer = (f"<p style='margin:24px 0 0;border-top:1px solid {BORDER};padding-top:16px;color:{NEUTRAL}'>{escape(closing)}</p>") if closing else ''
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>@media(max-width:480px){{.mail-body{{padding:20px 14px!important}}.mail-table td,.mail-table th{{padding:8px 4px!important}}.mail-table{{font-size:13px!important}}.mail-hero{{font-size:26px!important}}}}</style></head>
<body style="margin:0;padding:12px 4px;background:{BACKGROUND};font-family:{FONT};color:{INK};font-size:15px;line-height:1.6;font-variant-numeric:tabular-nums">
<table role="presentation" style="width:100%;border-collapse:collapse"><tr><td align="center">
<div class="mail-body" style="max-width:640px;margin:auto;background:#fff;padding:28px 26px;text-align:left">
<h1 style="font-family:{HEADING_FONT};font-size:27px;line-height:1.3;margin:0 0 8px;color:{INK}">{escape(title)}</h1>
<p style="margin:0 0 24px;font-size:14px;color:{NEUTRAL}">{escape(subtitle)}</p>
{content}{footer}</div></td></tr></table></body></html>'''
