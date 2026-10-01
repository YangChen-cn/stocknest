"""Optional local Gmail profile; environment variables always take precedence.

The profile is plaintext, ignored by Git, atomically written with mode 0600.
Never return it to logs, caches, cloud controls or configuration synchronization.
"""
import json
import os
from email.utils import parseaddr
from pathlib import Path

from stockwatch.storage import ROOT, ValidationError, atomic_write

NAMES = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL")


def profile_path(root: Path | None = None) -> Path:
    return (root or ROOT) / ".stockwatch" / "gmail.json"


def load_local(root: Path | None = None) -> dict:
    path = profile_path(root)
    if not path.exists():
        return {}
    try:
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != set(NAMES) or any(not isinstance(v, str) for v in raw.values()):
            raise ValueError
        return raw
    except (OSError, ValueError, TypeError):
        raise ValidationError("Local Gmail profile is invalid or not private; check file permissions.") from None


def credentials(root: Path | None = None) -> dict:
    environment = {name: os.environ.get(name, "").strip() for name in NAMES}
    local = {} if all(environment.values()) else load_local(root)
    return {name: environment[name] or local.get(name, "") for name in NAMES}


def save_local(address: str, recipient: str, password: str, root: Path | None = None) -> None:
    address, recipient = address.strip(), recipient.strip() or address.strip()
    for value in (address, recipient):
        if any(c in value for c in "\r\n") or parseaddr(value)[1] != value or "@" not in value:
            raise ValidationError("Invalid email address configuration.")
    password = "".join(password.split()) or load_local(root).get("GMAIL_APP_PASSWORD", "")
    if not password or any(ord(c) < 32 for c in password):
        raise ValidationError("Enter an App Password to configure local Gmail.")
    path = profile_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValidationError("Local Gmail profile is invalid or not private; check file permissions.")
    atomic_write(path, json.dumps(dict(zip(NAMES, (address, password, recipient))), indent=2) + "\n")


def remove_local(root: Path | None = None) -> None:
    profile_path(root).unlink(missing_ok=True)
