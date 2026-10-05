from copy import deepcopy
from datetime import date, datetime
import json

import pandas as pd
import pytest

from stockwatch.daily import run
from stockwatch.notifications.email import EmailDeliveryError
from stockwatch.periodic import period_metrics
from stockwatch.providers.base import Quote
from stockwatch.report_settings import report_settings, scheduled_due, summary_period
from stockwatch.scheduler_sync import SchedulerError, apply
from stockwatch.storage import load_state, validate_config


def test_defaults_backward_compatible_and_schedule_validation():
    assert 'reports' not in validate_config({'watchlist': {}})
    assert report_settings()['CLOSE']['enabled'] and not report_settings()['WEEKLY']['enabled']
    for bad in ({'CLOSE': {'time': '25:00'}}, {'INTRADAY': {'time': '18:00'}},
                {'CLOSE': {'days': []}},
                {'MONTHLY': {'enabled': 'false'}}, {'CLOSE': {'days': [True]}},
                {'CLOSE': {'days': [0, 0]}}, {'CLOSE': {'time': '09:30'}}):
        with pytest.raises(ValueError):
            report_settings(bad)


def test_due_settings_weekends_dst_monthly_holiday():
    config = {'reports': {'MONTHLY': {'enabled': True}, 'WEEKLY': {'enabled': True}}}
    assert not scheduled_due(config, 'CLOSE', datetime.fromisoformat('2026-10-02T22:52:00+00:00'))
    assert scheduled_due(config, 'CLOSE', datetime.fromisoformat('2026-10-02T23:20:00+00:00'))
    assert scheduled_due(config, 'CLOSE', datetime.fromisoformat('2026-11-03T23:54:00+00:00'))
    assert not scheduled_due(config, 'CLOSE', datetime.fromisoformat('2026-10-03T23:00:00+00:00'))
    # Hong Kong Saturday 10:52 remains Friday in New York, in both seasons.
    assert not scheduled_due(config, 'MONTHLY', datetime.fromisoformat('2027-01-02T02:51:00+00:00'))
    assert scheduled_due(config, 'MONTHLY', datetime.fromisoformat('2027-01-02T02:52:00+00:00'))
    assert not scheduled_due(config, 'MONTHLY', datetime.fromisoformat('2027-01-09T02:52:00+00:00'))
    assert scheduled_due(config, 'WEEKLY', datetime.fromisoformat('2026-10-03T02:52:00+00:00'))
    assert scheduled_due(config, 'WEEKLY', datetime.fromisoformat('2026-11-07T02:52:00+00:00'))
    assert not scheduled_due(config, 'WEEKLY', datetime.fromisoformat('2026-10-04T02:52:00+00:00'))
    assert scheduled_due(config, 'MONTHLY', datetime.fromisoformat('2026-11-01T02:52:00+00:00'))
    assert not scheduled_due(config, 'MONTHLY', datetime.fromisoformat('2026-11-07T02:52:00+00:00'))
    assert not scheduled_due({'reports': {'CLOSE': {'enabled': False}}}, 'CLOSE', datetime.fromisoformat('2026-10-02T23:00:00+00:00'))


def test_period_boundaries_and_flow_metrics():
    assert summary_period('MONTHLY', date(2026, 10, 1)) == (date(2026, 9, 1), date(2026, 9, 30), '2026-09')
    assert summary_period('WEEKLY', date(2027, 1, 1))[2] == '2026-W53'
    history = {'points': [
        dict(date='2026-09-25', nav=100, benchmark_nav=100, daily_pl=0, inflow=0, outflow=0, realized_pl=0, fees=0),
        dict(date='2026-09-28', nav=100, benchmark_nav=101, daily_pl=0, inflow=90, outflow=0, realized_pl=0, fees=0),
        dict(date='2026-09-29', nav=110, benchmark_nav=102, daily_pl=9, inflow=0, outflow=99, realized_pl=9, fees=1)]}
    values = period_metrics(history, date(2026, 9, 28), date(2026, 9, 29))
    assert values['portfolio_return_pct'] == 10 and values['profit_usd'] == 9
    assert values['inflow_usd'] == 90 and values['outflow_usd'] == 99 and values['fees_usd'] == 1
    history['points'][1]['nav'] = None
    assert period_metrics(history, date(2026, 9, 28), date(2026, 9, 29))['portfolio_return_pct'] is None
    assert period_metrics(history, date(2026, 9, 28), date(2026, 9, 30))['profit_usd'] is None


class SummaryProvider:
    def get_history_range(self, symbol, start, end):
        from stockwatch.history import sessions
        return pd.DataFrame([{'date': d, 'close': 90, 'split_ratio': 0} for d in sessions(start, end)])

    def get_quote(self, symbol, session=None):
        return Quote(symbol, 90, 90, session=session)


@pytest.mark.parametrize('mode,session,key', [('WEEKLY', date(2026, 10, 6), '2026-W41'),
                                              ('MONTHLY', date(2026, 11, 2), '2026-10')])
def test_summary_delivery_state_isolated_dedup_and_attachment(portfolio_files, monkeypatch, mode, session, key):
    for name, value in {'GMAIL_ADDRESS': 'sender@example.com', 'GMAIL_APP_PASSWORD': 'fake', 'REPORT_EMAIL': 'reader@example.com'}.items():
        monkeypatch.setenv(name, value)
    args = {**portfolio_files, 'provider': SummaryProvider(), 'session': session, 'mode': mode}
    sent = []
    assert run(**args, sender=lambda report, settings: sent.append(report)) == 0
    assert len(sent) == 1
    state = load_state(args['state_path'])
    assert state == {'_meta': {f'last_{mode.lower()}_period': key}}
    payload = json.loads(sent[0].data_json)
    assert payload['mode'] == mode and payload['new_alerts'] == [] and payload['period_summary']['portfolio_return_pct'] is not None
    assert run(**args, sender=lambda *a: sent.append(a)) == 0 and len(sent) == 1
    assert run(**args, force_send=True, sender=lambda *a: sent.append(a)) == 0 and len(sent) == 2
    assert not (args['state_path'].parent / 'performance.json').exists()


def test_summary_dryrun_failure_and_missing_do_not_consume(portfolio_files, monkeypatch):
    for name in ('GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'REPORT_EMAIL'):
        monkeypatch.setenv(name, 'fake@example.com')
    args = {**portfolio_files, 'provider': SummaryProvider(), 'mode': 'WEEKLY'}
    def fail(*args):
        raise EmailDeliveryError('safe failure')
    assert run(**args, dry_run=True, sender=fail) == 0
    assert run(**args, sender=fail) == 1
    assert load_state(args['state_path']) == {}
    monkeypatch.delenv('GMAIL_APP_PASSWORD')
    assert run(**args, sender=fail) == 0
    assert load_state(args['state_path']) == {}


def test_external_api_preserves_dispatch_credentials_and_job_scope(capsys):
    endpoint = 'https://api.github.com/repos/example/tracker/actions/workflows/daily.yml/dispatches'
    template = {'jobId': 10, 'url': endpoint, 'extendedData': {'headers': {'Authorization': 'Bearer fake-private-value'},
                'body': json.dumps({'ref': 'main', 'inputs': {'mode': 'CLOSE', 'scheduled': True}})}}
    calls = []
    def request(method, path, payload=None):
        calls.append((method, path, deepcopy(payload)))
        if path == '/jobs' and method == 'GET':
            return {'jobs': [template, {'jobId': 99, 'url': 'https://unrelated.example/'}]}
        if method == 'GET':
            return {'jobDetails': template}
        return {'jobId': 11}
    apply({'reports': {'WEEKLY': {'enabled': True}, 'MONTHLY': {'enabled': True}, 'CLOSE': {'enabled': False}}},
          'example/tracker', desired='cron-job.org', request=request, sleeper=lambda _: None)
    mutations = [p['job'] for method, _, p in calls if method in ('PATCH', 'PUT')]
    assert len(mutations) == 4
    assert all(job['extendedData']['headers'] == template['extendedData']['headers'] for job in mutations)
    assert [job['schedule']['timezone'] for job in mutations] == ['America/New_York', 'America/New_York', 'Asia/Hong_Kong', 'Asia/Hong_Kong']
    assert mutations[1]['enabled'] is False and mutations[2]['schedule']['wdays'] == [6]
    assert mutations[2]['schedule']['hours'] == [10] and mutations[2]['schedule']['minutes'] == [52]
    assert mutations[3]['schedule']['wdays'] == [0, 6]
    assert mutations[3]['schedule']['mdays'] == list(range(1, 8))
    assert all('99' not in path for _, path, _ in calls)
    assert 'fake-private-value' not in capsys.readouterr().out
    with pytest.raises(SchedulerError):
        apply({}, 'example/tracker', desired='cron-job.org', request=lambda *a: {'someFailed': True}, sleeper=lambda _: None)


def test_cheap_preflight_skips_disabled_and_sent_without_calendar():
    from stockwatch.schedule import configured_slot
    now = datetime.fromisoformat('2026-10-02T23:00:00+00:00')
    assert configured_slot({}, 'CLOSE', now, {})
    assert not configured_slot({}, 'CLOSE', now, {'_meta': {'last_report_session': '2026-10-02'}})
    config = {'reports': {'MONTHLY': {'enabled': True}, 'WEEKLY': {'enabled': True}}}
    weekend = datetime.fromisoformat('2026-10-03T02:52:00+00:00')
    assert configured_slot(config, 'MONTHLY', weekend, {})
    assert not configured_slot(config, 'MONTHLY', weekend, {'_meta': {'last_monthly_period': '2026-09'}})
    assert configured_slot(config, 'WEEKLY', weekend, {})
    assert not configured_slot(config, 'WEEKLY', weekend, {'_meta': {'last_weekly_period': '2026-W40'}})
    assert not configured_slot({'reports': {'CLOSE': {'enabled': False}}}, 'CLOSE', now, {})


def test_period_failure_and_existing_alerts_preserved(portfolio_files, monkeypatch):
    for name in ('GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'REPORT_EMAIL'):
        monkeypatch.setenv(name, 'fake@example.com')
    initial = {'XYZ': {'below_95': {'triggered': True, 'last_notified': '2026-10-05'}},
               '_meta': {'last_report_session': '2026-10-05', 'last_intraday_session': '2026-10-06'}}
    portfolio_files['state_path'].write_text(json.dumps(initial))
    class Missing(SummaryProvider):
        def get_history_range(self, *args):
            return pd.DataFrame(columns=['date', 'close'])
    assert run(**{**portfolio_files, 'provider': Missing()}, mode='WEEKLY', sender=lambda *a: pytest.fail('must not send')) == 1
    assert load_state(portfolio_files['state_path']) == initial
    assert run(**{**portfolio_files, 'provider': SummaryProvider()}, mode='WEEKLY', sender=lambda *a: None) == 0
    final = load_state(portfolio_files['state_path'])
    assert final['XYZ'] == initial['XYZ']
    assert final['_meta'] == {**initial['_meta'], 'last_weekly_period': '2026-W41'}


def test_scheduler_workflow_is_private_and_lightweight():
    import yaml
    from stockwatch.storage import ROOT
    workflow = yaml.load((ROOT / '.github/workflows/scheduler.yml').read_text(), Loader=yaml.BaseLoader)
    # contents:write persists the scheduler trigger after a successful apply.
    assert workflow['permissions'] == {'contents': 'write'}
    inputs = workflow['on']['workflow_dispatch']['inputs']['desired_trigger']
    assert inputs['options'] == ['native', 'cron-job.org'] and inputs['default'] == 'cron-job.org'
    assert 'github.event.repository.private == true' in workflow['jobs']['apply']['if']
    steps = workflow['jobs']['apply']['steps']
    assert not any('requirements-runtime' in step.get('run', '') or 'pytest' in step.get('run', '') for step in steps)
    assert any('--apply-trigger' in step.get('run', '') for step in steps)
    persist = next(step['run'] for step in steps if step.get('name') == 'Persist scheduler trigger')
    # Two-phase switch: the trigger is committed only after the jobs were synced.
    assert 'git add -- config.yaml' in persist and '[skip ci]' in persist
    assert 'git diff --quiet -- config.yaml' in persist


def test_weekend_migration_and_cross_month_reference():
    plans = report_settings({'WEEKLY': {'enabled': True, 'time': '18:53', 'days': [4]},
                             'MONTHLY': {'enabled': False, 'time': '18:53', 'days': [0, 1, 2, 3, 4]}})
    assert plans['WEEKLY'] == {'enabled': True, 'time': '10:52'}
    assert plans['MONTHLY'] == {'enabled': False, 'time': '10:52'}
    assert summary_period('MONTHLY', date(2026, 8, 1)) == (date(2026, 7, 1), date(2026, 7, 31), '2026-07')
    # Good Friday: the weekly cutoff is Thursday, but week dedupe is unchanged.
    assert summary_period('WEEKLY', date(2026, 4, 4)) == (date(2026, 3, 30), date(2026, 4, 2), '2026-W14')


def test_weekend_scheduled_cli_uses_delivery_month_and_latest_close(portfolio_files, monkeypatch):
    import stockwatch.daily as daily
    from stockwatch.storage import load_config, save_config
    config = load_config(portfolio_files['config_path'])
    config['reports'] = {'MONTHLY': {'enabled': True}}
    save_config(portfolio_files['config_path'], config)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat('2026-08-01T02:52:00+00:00').astimezone(tz)
    monkeypatch.setattr(daily, 'datetime', Clock)
    monkeypatch.setattr(daily, 'OpenBBProvider', SummaryProvider)
    result = daily.main(['--mode', 'MONTHLY', '--scheduled', '--dry-run', '--skip-hsbc',
                         '--config', str(portfolio_files['config_path']), '--transactions', str(portfolio_files['transactions_path']),
                         '--state', str(portfolio_files['state_path']), '--output-dir', str(portfolio_files['output_dir']),
                         '--log-dir', str(portfolio_files['output_dir'] / 'logs')])
    assert result == 0
    report = (portfolio_files['output_dir'] / 'monthly-2026-07.txt').read_text()
    assert '2026-07-31' in report and '2026-07-01' in report
    assert not (portfolio_files['output_dir'] / 'monthly-2026-06.txt').exists()
    assert load_state(portfolio_files['state_path']) == {}
