"""Gmail SMTP delivery. No credentials in portfolio config, logs or exception messages."""
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr

from stockwatch.i18n import UserFacingError

from stockwatch.reports import Report
from stockwatch.notifications.local import credentials

ENV_NAMES = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL")


@dataclass(frozen=True, repr=False)
class EmailSettings:
    address: str
    password: str
    recipient: str

    @classmethod
    def from_environment(cls) -> "EmailSettings | None":
        profile = credentials()
        values = [profile[name] for name in ENV_NAMES]
        return cls(values[0], "".join(values[1].split()), values[2]) if all(values) else None


class EmailDeliveryError(UserFacingError, RuntimeError):
    """Safe error with no SMTP response or credentials."""


def configuration_status() -> dict[str, bool]:
    return {name: bool(value) for name, value in credentials().items()}


def make_message(report: Report, settings: EmailSettings) -> EmailMessage:
    for address in (settings.address, settings.recipient):
        if "\n" in address or "\r" in address or parseaddr(address)[1] != address or "@" not in address:
            raise EmailDeliveryError("Invalid email address configuration.")
    message = EmailMessage()
    message["Subject"] = report.subject
    message["From"] = settings.address
    message["To"] = settings.recipient
    message.set_content(report.text)
    html = report.html
    for image in report.inline_images:
        html = html.replace(image.data_url, f"cid:{image.cid}")
    message.add_alternative(html, subtype="html")
    html_part = message.get_payload()[1]
    for image in report.inline_images:
        html_part.add_related(image.data, maintype="image", subtype="png", cid=f"<{image.cid}>", disposition="inline")
    if report.data_json is not None:
        message.add_attachment(report.data_json.encode("utf-8"), maintype="application", subtype="json",
                               filename=report.data_filename or "stockwatch-report.json", cte="base64")
    return message


def send_report(report: Report, settings: EmailSettings) -> None:
    message = make_message(report, settings)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(settings.address, settings.password)
            refused = smtp.send_message(message)
            if refused:
                raise EmailDeliveryError("Gmail refused the report recipient.")
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError("Gmail delivery failed ({kind}); check Secrets and account settings.", kind=type(exc).__name__) from None
