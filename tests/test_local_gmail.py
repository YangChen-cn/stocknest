import json
import stat

import pytest

from stockwatch.notifications import local
from stockwatch.notifications.email import EmailSettings
from stockwatch.storage import ValidationError


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "ROOT", tmp_path)
    for name in local.NAMES:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_optional_profile_atomic_private_permissions_and_environment_precedence(profile, monkeypatch):
    assert EmailSettings.from_environment() is None
    local.save_local("sender@example.com", "", "synthetic-password")
    path = local.profile_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    settings = EmailSettings.from_environment()
    assert settings.recipient == "sender@example.com" and settings.password == "synthetic-password"
    assert "synthetic-password" not in repr(settings)
    local.save_local("new@example.com", "report@example.com", "")
    assert local.load_local()["GMAIL_APP_PASSWORD"] == "synthetic-password"
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "environment-password")
    assert EmailSettings.from_environment().password == "environment-password"
    local.remove_local()
    assert not path.exists() and EmailSettings.from_environment() is None


def test_invalid_save_and_atomic_failure_preserve_profile(profile, monkeypatch):
    local.save_local("sender@example.com", "report@example.com", "synthetic-password")
    original = local.profile_path().read_bytes()
    with pytest.raises(ValidationError):
        local.save_local("bad\r\n@example.com", "", "new-password")
    monkeypatch.setattr(local, "atomic_write", lambda *a: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(OSError):
        local.save_local("new@example.com", "", "new-password")
    assert local.profile_path().read_bytes() == original


def test_insecure_profile_and_corrupt_json_are_safe_errors(profile):
    local.save_local("sender@example.com", "", "synthetic-password")
    path = local.profile_path()
    path.chmod(0o644)
    with pytest.raises(ValidationError) as error:
        local.load_local()
    assert "synthetic-password" not in str(error.value)
    path.chmod(0o600)
    path.write_text(json.dumps(["synthetic-password"]))
    with pytest.raises(ValidationError):
        local.load_local()
