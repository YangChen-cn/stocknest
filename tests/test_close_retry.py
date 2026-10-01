from unittest.mock import Mock

from stockwatch.daily import run
from stockwatch.imports.hsbc import HSBCSyncError
from stockwatch.providers.base import Quote
from stockwatch.storage import load_state
from conftest import DAY, FakeProvider


def credentials(monkeypatch):
    for key, value in {"GMAIL_ADDRESS":"sender@example.com", "GMAIL_APP_PASSWORD":"synthetic-secret", "REPORT_EMAIL":"reader@example.com"}.items():
        monkeypatch.setenv(key, value)


class Delayed(FakeProvider):
    def __init__(self, failures):
        super().__init__()
        self.calls = self.cleared = 0
        self.failures = failures

    def get_quote(self, symbol, session=None):
        self.calls += 1
        return Quote(symbol, session=DAY, error="Data unavailable") if self.calls <= self.failures else super().get_quote(symbol, session)

    def clear_cache(self):
        self.cleared += 1


def test_waits_without_consuming_state_then_sends_complete_report(portfolio_files, monkeypatch):
    credentials(monkeypatch)
    provider = Delayed(2)
    sent, sleeps = [], []
    def sleep(seconds):
        assert not sent and load_state(portfolio_files["state_path"]) == {}
        sleeps.append(seconds)
    assert run(**{**portfolio_files, "provider":provider}, sender=lambda *a: sent.append(a[0]), sleeper=sleep) == 0
    assert sleeps == [120,120] and provider.calls == 3 and provider.cleared == 2 and len(sent) == 1
    assert "report error" not in sent[0].subject
    assert load_state(portfolio_files["state_path"])["_meta"]["last_report_session"] == DAY.isoformat()


def test_exhaustion_notifies_once_and_recovery_sends_normal_report(portfolio_files, monkeypatch):
    credentials(monkeypatch)
    sent, sleeps = [], []
    provider = Delayed(10)
    args = {**portfolio_files, "provider":provider,"sender":lambda *a:sent.append(a[0]),"sleeper":sleeps.append}
    assert run(**args) == 2
    state = load_state(portfolio_files["state_path"])
    assert state == {"_meta":{"last_close_error_session":DAY.isoformat()}}
    assert sleeps == [120,120] and len(sent) == 1 and "report error" in sent[0].subject
    assert "Market Value" not in sent[0].text
    assert run(**args) == 2 and len(sent) == 1 and sleeps == [120,120]
    args["provider"] = FakeProvider()
    assert run(**args) == 0 and len(sent) == 2
    assert load_state(portfolio_files["state_path"])["XYZ"]["below_95"]["triggered"]
    assert run(**args) == 0 and len(sent) == 2


def test_preview_never_waits_or_sends_and_missing_mail_never_waits(portfolio_files, monkeypatch):
    credentials(monkeypatch)
    forbidden = Mock(side_effect=AssertionError("must not sleep or send"))
    provider = Delayed(20)
    assert run(**{**portfolio_files,"provider":provider}, dry_run=True,sender=forbidden,sleeper=forbidden) == 0
    assert load_state(portfolio_files["state_path"]) == {}
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    assert run(**{**portfolio_files,"provider":provider},sender=forbidden,sleeper=forbidden) == 0
    assert provider.calls == 2


def test_error_smtp_failure_keeps_original_state(portfolio_files, monkeypatch):
    from stockwatch.notifications.email import EmailDeliveryError
    credentials(monkeypatch)
    def fail(*a):
        raise EmailDeliveryError("synthetic")
    assert run(**{**portfolio_files,"provider":Delayed(20)},sender=fail,sleeper=lambda _:None) == 1
    assert load_state(portfolio_files["state_path"]) == {}


def test_import_precedes_snapshot_and_failure_withholds_normal_report(portfolio_files, monkeypatch):
    credentials(monkeypatch)
    sent=[]
    def importer(*a, **kw):
        raise HSBCSyncError("Gmail read-only sync failed; check App Password and IMAP access.")
    assert run(**portfolio_files,importer=importer,sender=lambda *a:sent.append(a[0])) == 2
    assert "HSBC sync failed" in sent[0].text and "last_report_session" not in load_state(portfolio_files["state_path"])["_meta"]


def test_known_error_and_force_still_cannot_duplicate_error_mail(portfolio_files, monkeypatch):
    credentials(monkeypatch)
    sent=[]
    args={**portfolio_files,"provider":Delayed(20),"sender":lambda *a:sent.append(a[0]),"sleeper":lambda _:None}
    assert run(**args)==2
    assert run(**args,force_send=True)==2 and len(sent)==1


def test_imported_ledger_is_used_by_the_report(portfolio_files,monkeypatch):
    from stockwatch.storage import save_transactions
    credentials(monkeypatch)
    sent=[]
    def importer(config,ledger,state,**kw):
        save_transactions(ledger,[{"date":"2026-10-05","symbol":"XYZ","side":"BUY","shares":"3","price":"80","note":"synthetic"}])
        return {"imported":1,"duplicates":0,"skipped":0}
    assert run(**portfolio_files,importer=importer,sender=lambda *a:sent.append(a[0]))==0
    assert "$270.00" in sent[0].text


def test_daily_import_caps_historical_window_to_three_days(portfolio_files):
    from stockwatch.storage import load_config, save_config
    config=load_config(portfolio_files["config_path"])
    config["imports"]={"hsbc":{"enabled":True,"lookback_days":30}}
    save_config(portfolio_files["config_path"],config)
    captured=[]
    def importer(config,*args,**kwargs):
        captured.append(config["imports"]["hsbc"]["lookback_days"])
        return {"disabled":True}
    assert run(**portfolio_files,dry_run=True,importer=importer)==0
    assert captured==[3]
    assert load_config(portfolio_files["config_path"])["imports"]["hsbc"]["lookback_days"]==30
