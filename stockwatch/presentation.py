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


def _balanced_english_lines(line: str, width=28) -> list[str]:
    """Short, similar-length verse lines even in clients without text-wrap support."""
    words = line.split()
    count = min(len(words), max(1, (len(line) + width - 1) // width))
    lines = []
    while count > 1:
        target = len(' '.join(words)) / count
        split = min(range(1, len(words) - count + 2),
                    key=lambda index: abs(len(' '.join(words[:index])) - target))
        lines.append(' '.join(words[:split]))
        words = words[split:]
        count -= 1
    return lines + [' '.join(words)]


def reflection_html(day: date, lang: str) -> str:
    original_language, (words, author, work, source, gloss) = classic(day)
    note = (f"<p style='font-size:12px;color:{NEUTRAL};line-height:1.75;margin:12px 0 0;text-wrap:pretty'>{escape(t('Reading note', lang))} · {escape(gloss)}</p>"
            if original_language != lang else "")
    verse_lines = (words.replace("，", "，\n").splitlines() if original_language == "zh-CN"
                   else [part for line in words.splitlines() for part in _balanced_english_lines(line)])
    verse = "".join(f"<span style='display:block;text-wrap:balance'>{escape(line)}</span>" for line in verse_lines)
    return (f"<div class='sw-classic' style='padding:20px 18px;background:{BACKGROUND};border-radius:8px'>"
            f"<p lang='{original_language}' style='font-family:{HEADING_FONT};font-size:18px;line-height:1.8;margin:0;color:{INK}'>{verse}</p>"
            f"<p style='font-size:12px;color:{NEUTRAL};margin:12px 0 0'>— {escape(author)}</p>"
            f"<p style='font-size:12px;color:{NEUTRAL};line-height:1.6;margin:3px 0 0;text-wrap:pretty'>{escape(work)}"
            f"<span style='white-space:nowrap'> · <a href='{escape(source, quote=True)}' style='color:{GREEN}'>{escape(t('Source', lang))}</a></span></p>{note}</div>")


def atomic(value: str) -> str:
    """Keep one short fact (a date, amount, percentage or label) intact."""
    return f"<span style='display:inline-block;white-space:nowrap'>{escape(value)}</span>"


def email_metrics(rows) -> str:
    return ("<table role='presentation' style='width:100%;border-collapse:collapse'>"
            + "".join(f"<tr><td style='padding:9px 12px 9px 0;font-size:13px;line-height:1.5;color:{NEUTRAL};text-wrap:balance'>{escape(name)}</td>"
                      f"<td style='padding:9px 0;text-align:right;font-size:14px;font-weight:600;color:{color}'>{atomic(value)}</td></tr>"
                      for name, value, color in rows) + "</table>")


def email_hero(left_label, left_value, right_label, right_value, *, right_color=INK, detail="") -> str:
    extra = f"<div style='font-size:13px;margin-top:4px'>{atomic(detail)}</div>" if detail else ""
    return (f"<table role='presentation' class='mail-hero-grid' style='width:100%;border-collapse:collapse'><tr>"
            f"<td class='mail-hero-cell' style='vertical-align:top;padding:14px 12px 14px 0'>"
            f"<div style='font-size:12px;color:{NEUTRAL};margin-bottom:8px;text-wrap:balance'>{escape(left_label)}</div>"
            f"<div class='mail-hero' style='font-family:{HEADING_FONT};font-size:32px;font-weight:600;line-height:1.25'>{atomic(left_value)}</div></td>"
            f"<td class='mail-hero-cell mail-hero-right' style='vertical-align:top;text-align:right;padding:14px 0 14px 12px;color:{right_color}'>"
            f"<div style='font-size:12px;color:{NEUTRAL};margin-bottom:8px;text-wrap:balance'>{escape(right_label)}</div>"
            f"<div class='mail-hero' style='font-family:{HEADING_FONT};font-size:28px;font-weight:600;line-height:1.25'>{atomic(right_value)}</div>{extra}</td></tr></table>")


def email_holding(symbol, price, shares, details, lang, *, thesis="") -> str:
    rows = []
    for index in range(0, len(details), 2):
        cells = []
        for label, value, color in details[index:index + 2]:
            cells.append(f"<td style='width:50%;vertical-align:top;padding:8px 8px 4px 0'>"
                         f"<div style='font-size:11px;line-height:1.5;color:{NEUTRAL};text-wrap:balance'>{escape(label)}</div>"
                         f"<div style='font-size:13px;margin-top:3px;color:{color}'>{atomic(value)}</div></td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    note = (f"<div style='padding-top:12px;margin-top:12px;border-top:1px solid {BORDER};font-size:12px;color:{NEUTRAL}'>"
            f"<div style='font-size:11px;margin-bottom:4px'>{escape(t('Thesis', lang))}</div>"
            f"<div style='white-space:pre-wrap;overflow-wrap:break-word;text-wrap:pretty'>{escape(thesis)}</div></div>") if thesis else ""
    return (f"<div class='mail-holding' style='border:1px solid {BORDER};border-radius:8px;padding:14px 16px;margin:0 0 10px'>"
            f"<table role='presentation' style='width:100%;border-collapse:collapse'><tr>"
            f"<td style='vertical-align:top;font-size:15px;font-weight:600'>{escape(symbol)}"
            f"<div style='font-size:11px;font-weight:400;color:{NEUTRAL};margin-top:4px'>{atomic(t('{shares} shares', lang, shares=shares))}</div></td>"
            f"<td style='text-align:right;vertical-align:top'><div style='font-size:11px;color:{NEUTRAL}'>{escape(t('Current Price', lang))}</div>"
            f"<div style='font-size:15px;font-weight:600;margin-top:4px'>{atomic(price)}</div></td></tr></table>"
            f"<table role='presentation' style='width:100%;border-collapse:collapse;margin-top:8px'>{''.join(rows)}</table>{note}</div>")


def record_note(history, lang: str) -> str | None:
    points = history.get('points', []) if history else []
    # The first point is the pre-contribution baseline, not a day of performance.
    count = sum(p.get('nav') is not None for p in points[1:])
    return t('History covers {count} recorded trading days.', lang, count=count) if count else None


def email_section(title: str, content: str) -> str:
    return (f"<section style='margin-top:24px;border-top:1px solid {BORDER};padding-top:16px'>"
            f"<h2 style='font-family:{HEADING_FONT};font-size:20px;line-height:1.5;color:{INK};margin:0 0 14px;text-wrap:balance'>{escape(title)}</h2>{content}</section>")


def email_shell(title: str, subtitle: str, content: str, lang: str, *, closing: str | None = None,
                closing_html: str | None = None) -> str:
    footer = (f"<div style='margin-top:24px'>{closing_html or escape(closing)}</div>") if closing else ''
    if " · " in subtitle:
        first, rest = subtitle.split(" · ", 1)
        subtitle_body = atomic(first) + f"<span style='display:block;margin-top:3px;text-wrap:balance'>{escape(rest)}</span>"
    else:
        subtitle_body = " — ".join(atomic(part) for part in subtitle.split(" — "))
    brand = ""
    display_title = title
    if title.startswith("StockWatch "):
        brand = f"<div style='font-size:11px;letter-spacing:.12em;color:{GREEN};margin-bottom:10px'>STOCKWATCH</div>"
        display_title = title.split(" ", 1)[1]
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
p{{text-wrap:pretty}}h1,h2,h3{{text-wrap:balance}}
@media(max-width:480px){{.mail-body{{padding:22px 16px!important}}.mail-table td,.mail-table th{{padding:8px 4px!important}}.mail-table{{font-size:13px!important}}.mail-hero{{font-size:26px!important}}}}
@media(max-width:380px){{.mail-hero-cell{{display:block!important;width:auto!important;text-align:left!important;padding:12px 0!important}}.mail-hero-right{{border-top:1px solid {BORDER}}}}}
</style></head>
<body style="margin:0;padding:16px 4px;background:{BACKGROUND};font-family:{FONT};color:{INK};font-size:15px;line-height:1.65;font-variant-numeric:tabular-nums">
<table role="presentation" style="width:100%;border-collapse:collapse"><tr><td align="center">
<div class="mail-body" style="max-width:640px;margin:auto;background:#fff;padding:32px 28px;text-align:left">
{brand}<h1 aria-label="{escape(title)}" style="font-family:{HEADING_FONT};font-size:27px;line-height:1.4;margin:0 0 10px;color:{INK};text-wrap:balance">{escape(display_title)}</h1>
<p style="margin:0 0 24px;font-size:13px;line-height:1.75;color:{NEUTRAL};text-wrap:pretty">{subtitle_body}</p>
{content}{footer}</div></td></tr></table></body></html>'''
