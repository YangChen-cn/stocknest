"""Restricted cloud edits use fake GitHub responses, never real credentials."""
import base64
from copy import deepcopy
import io
import json
from urllib.error import HTTPError, URLError

import pytest
import yaml

from stockwatch.cloud_config import CloudConfigError, CloudRepository, CloudSettings, merge_watchlist
from stockwatch.storage import ValidationError, validate_config


class FakeGitHub:
    def __init__(self, raw=None):
        self.raw = raw or {'watchlist': {'XYZ': {'thesis': 'Original', 'buy_below': 10}}}
        self.requests = []
        self.private = True
        self.branch = 'main'
        self.sha = 'a' * 40
        self.failure = None

    def __call__(self, request, timeout):
        self.requests.append(request)
        assert timeout == 15
        root = 'https://api.github.com/repos/Example/private'
        assert request.full_url == root or request.full_url.startswith(root + '/contents/config.yaml')
        if request.method == 'PUT':
            if self.failure:
                raise self.failure
            body = json.loads(request.data)
            assert body['sha'] == self.sha and body['branch'] == 'main'
            assert request.full_url.endswith('/contents/config.yaml')
            assert body['message'].endswith('[skip ci]')
            self.raw = yaml.safe_load(base64.b64decode(body['content']))
            return io.BytesIO(b'{"commit": {"sha": "saved"}}')
        if request.full_url.endswith('/contents/config.yaml?ref=main'):
            value = {'type': 'file', 'encoding': 'base64', 'sha': self.sha,
                     'content': base64.b64encode(yaml.safe_dump(self.raw).encode()).decode()}
        else:
            value = {'private': self.private, 'default_branch': self.branch}
        return io.BytesIO(json.dumps(value).encode())


def repository(fake):
    return CloudRepository(CloudSettings('Example/private', 'test_only'), opener=fake)


def test_cloud_write_merges_independent_fields_and_preserves_other_settings():
    raw = {'portfolio': {'language': 'zh-CN', 'benchmark': 'SPY'},
           'notifications': {'email_enabled': False}, 'imports': {'hsbc': {'enabled': False}},
           'watchlist': {'XYZ': {'thesis': 'Original', 'buy_below': 10}}}
    fake = FakeGitHub(deepcopy(raw))
    base = validate_config(raw)['watchlist']
    desired = deepcopy(base)
    desired['XYZ']['thesis'] = 'New note'
    # Concurrent target edit and another stock survive the notes-only save.
    fake.raw['watchlist']['XYZ']['buy_below'] = 12
    fake.raw['watchlist']['OTHER'] = {'thesis': 'Remote'}
    result = repository(fake).save_watchlist(base, desired)
    assert result['changed']
    assert fake.raw['watchlist']['XYZ'] == {'thesis': 'New note', 'buy_below': 12}
    assert fake.raw['watchlist']['OTHER']['thesis'] == 'Remote'
    for key in ('portfolio', 'notifications', 'imports'):
        assert fake.raw[key] == raw[key]
    assert fake.requests[0].full_url == 'https://api.github.com/repos/Example/private'
    assert sum(req.method == 'PUT' for req in fake.requests) == 1
    assert all('test_only' not in req.full_url for req in fake.requests)


def test_pure_observation_add_and_remove_rules():
    fake = FakeGitHub()
    base = repository(fake).read_config()['config']['watchlist']
    desired = deepcopy(base)
    desired['NEW'] = {'thesis': '', 'status': 'watching'}
    desired['XYZ'].pop('buy_below')
    repository(fake).save_watchlist(base, desired)
    assert 'NEW' in fake.raw['watchlist'] and 'buy_below' not in fake.raw['watchlist']['XYZ']
    desired.pop('XYZ')
    repository(fake).save_watchlist(validate_config(fake.raw)['watchlist'], desired)
    assert 'XYZ' not in fake.raw['watchlist']


@pytest.mark.parametrize('action', ['change', 'delete', 'add'])
def test_same_field_and_entry_conflicts_do_not_write(action):
    fake = FakeGitHub()
    base = validate_config(fake.raw)['watchlist']
    desired = deepcopy(base)
    if action == 'change':
        desired['XYZ']['thesis'] = 'Local'
        fake.raw['watchlist']['XYZ']['thesis'] = 'Remote'
    elif action == 'delete':
        desired.pop('XYZ')
        fake.raw['watchlist']['XYZ']['buy_below'] = 13
    else:
        desired['NEW'] = {'thesis': 'Local'}
        fake.raw['watchlist']['NEW'] = {'thesis': 'Remote'}
    before = deepcopy(fake.raw)
    with pytest.raises(CloudConfigError, match='changed elsewhere'):
        repository(fake).save_watchlist(base, desired)
    assert fake.raw == before and not any(req.method == 'PUT' for req in fake.requests)


def test_already_saved_edit_is_noop():
    fake = FakeGitHub()
    base = validate_config(fake.raw)['watchlist']
    desired = deepcopy(base)
    desired['XYZ']['thesis'] = 'Done'
    fake.raw['watchlist']['XYZ']['thesis'] = 'Done'
    result = repository(fake).save_watchlist(base, desired)
    assert not result['changed'] and not any(req.method == 'PUT' for req in fake.requests)


@pytest.mark.parametrize('private,branch', [(False, 'main'), (True, 'dev')])
def test_private_default_branch_is_required(private, branch):
    fake = FakeGitHub()
    fake.private, fake.branch = private, branch
    with pytest.raises(CloudConfigError, match='verified private'):
        repository(fake).save_watchlist({}, {'NEW': {}})
    assert len(fake.requests) == 1


def test_invalid_input_rejected_before_requests():
    fake = FakeGitHub()
    with pytest.raises(ValidationError):
        repository(fake).save_watchlist({}, {'XYZ': {'buy_below': -1}})
    assert not fake.requests
    fake.sha = 'invalid'
    with pytest.raises(CloudConfigError, match='configuration is invalid'):
        repository(fake).read_config()


@pytest.mark.parametrize('code,message', [(409, 'changed during saving'), (401, 'HTTP 401, config.yaml'), (403, 'HTTP 403, config.yaml'), (500, 'could not be confirmed')])
def test_upstream_failures_never_echo_secrets(code, message, caplog):
    fake = FakeGitHub()
    fake.failure = HTTPError('https://example.invalid/test_only', code, 'test_only', {}, io.BytesIO(b'test_only'))
    before = deepcopy(fake.raw)
    with pytest.raises(CloudConfigError, match=message) as error:
        repository(fake).save_watchlist({}, {'NEW': {}})
    assert 'test_only' not in str(error.value) + caplog.text
    assert fake.raw == before


def test_timeout_and_credentials_are_safe(monkeypatch):
    fake = FakeGitHub()
    fake.failure = URLError('test_only')
    with pytest.raises(CloudConfigError, match='timed out') as error:
        repository(fake).save_watchlist({}, {'NEW': {}})
    assert 'test_only' not in str(error.value)
    assert 'test_only' not in repr(repository(fake).settings)
    monkeypatch.delenv('STOCKWATCH_CLOUD_TOKEN', raising=False)
    monkeypatch.delenv('STOCKWATCH_CLOUD_REPOSITORY', raising=False)
    with pytest.raises(CloudConfigError, match='not configured'):
        CloudSettings.from_environment()
    monkeypatch.setenv('STOCKWATCH_CLOUD_REPOSITORY', 'Example/private')
    monkeypatch.setenv('STOCKWATCH_CLOUD_TOKEN', 'test_only\ninvalid')
    with pytest.raises(CloudConfigError, match='invalid format'):
        CloudSettings.from_environment()


def test_nested_alert_changes_merge_but_conflicting_deletion_stops():
    base = {'XYZ': {'alerts': {'below': 10, 'daily_move_pct': 5}}}
    desired = {'XYZ': {'alerts': {'below': 11, 'daily_move_pct': 5}}}
    current = {'XYZ': {'alerts': {'below': 10, 'daily_move_pct': 7}}}
    assert merge_watchlist(base, desired, current)['XYZ']['alerts'] == {'below': 11, 'daily_move_pct': 7}
    with pytest.raises(CloudConfigError):
        merge_watchlist(base, {'XYZ': {}}, current)


def test_scheduler_trigger_single_field_cloud_write():
    raw = {'portfolio': {'language': 'en'}, 'watchlist': {'XYZ': {'thesis': 'Note'}}}
    fake = FakeGitHub(deepcopy(raw))
    result = repository(fake).save_scheduler_trigger('native', 'cron-job.org')
    assert result['changed'] and fake.raw['scheduler'] == {'trigger': 'cron-job.org'}
    assert fake.raw['watchlist']['XYZ'] == {'thesis': 'Note'}  # Other sections ride along untouched.
    assert not repository(fake).save_scheduler_trigger('cron-job.org', 'cron-job.org')['changed']
    fake.raw['scheduler'] = {'trigger': 'native'}  # Changed elsewhere after the draft was opened.
    with pytest.raises(CloudConfigError, match='elsewhere'):
        repository(fake).save_scheduler_trigger('cron-job.org', 'cron-job.org')
    with pytest.raises(CloudConfigError, match='native or cron-job.org'):
        repository(fake).save_scheduler_trigger('native', 'hourly')
