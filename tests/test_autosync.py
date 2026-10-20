from contextlib import nullcontext
from copy import deepcopy

import pytest

from stockwatch import autosync
from stockwatch.git_sync import SyncError


@pytest.fixture
def pending(monkeypatch, tmp_path):
    state = {'enabled': True, 'pending_since': None}
    calls = []
    monkeypatch.setattr(autosync, 'sync_lock', lambda root: nullcontext())
    monkeypatch.setattr(autosync, 'status', lambda root: deepcopy(state))
    monkeypatch.setattr(autosync, '_save', lambda root, value: state.update(value))
    monkeypatch.setattr(autosync, '_pending', lambda root: (True, False))
    monkeypatch.setattr(autosync, 'repository', lambda root: 'Example/private')
    monkeypatch.setattr(autosync, '_sync', lambda root: calls.append(root))
    return tmp_path, state, calls


def test_first_pending_change_sets_one_hour_deadline_without_resetting(pending):
    root, state, calls = pending
    assert autosync.check(root, now=100) == 'pending'
    assert autosync.check(root, now=3699) == 'pending'
    assert state['pending_since'] == 100 and not calls
    assert autosync.check(root, now=3700) == 'synced'
    assert len(calls) == 1 and state['pending_since'] is None


def test_failure_keeps_pending_edits_and_waits_five_minutes(pending, monkeypatch):
    root, state, calls = pending
    state['pending_since'] = 100
    def fail(root):
        calls.append(root)
        raise SyncError('Git unavailable or timed out. Check your terminal and network, then retry.')
    monkeypatch.setattr(autosync, '_sync', fail)
    assert autosync.check(root, now=3700) == 'failed'
    assert state['pending_since'] == 100
    assert autosync.check(root, now=3999) == 'retry_later'
    assert len(calls) == 1
    assert autosync.check(root, now=4000) == 'failed' and len(calls) == 2


def test_unrelated_changes_and_dry_run_never_push(pending, monkeypatch):
    root, state, calls = pending
    state['pending_since'] = 100
    assert autosync.check(root, now=3700, dry_run=True) == 'due'
    assert state == {'enabled': True, 'pending_since': 100} and not calls
    monkeypatch.setattr(autosync, '_pending', lambda root: (True, True))
    assert autosync.check(root, now=3700) == 'blocked' and not calls
    state['enabled'] = False
    assert autosync.check(root, now=3800) == 'disabled' and not calls
