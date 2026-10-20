from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

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
    def push(root):
        calls.append(root)
        monkeypatch.setattr(autosync, '_pending', lambda root: (False, False))
    monkeypatch.setattr(autosync, '_sync', push)
    return tmp_path, state, calls


def test_first_pending_change_sets_ten_minute_deadline_without_resetting(pending):
    root, state, calls = pending
    assert autosync.check(root, now=100) == 'pending'
    assert autosync.check(root, now=699) == 'pending'
    assert state['pending_since'] == 100 and not calls
    assert autosync.check(root, now=700) == 'synced'
    assert len(calls) == 1 and state['pending_since'] is None


def test_failure_keeps_pending_edits_and_waits_five_minutes(pending, monkeypatch):
    root, state, calls = pending
    state['pending_since'] = 100
    def fail(root):
        calls.append(root)
        raise SyncError('Git unavailable or timed out. Check your terminal and network, then retry.')
    monkeypatch.setattr(autosync, '_sync', fail)
    assert autosync.check(root, now=700) == 'failed'
    assert state['pending_since'] == 100
    assert autosync.check(root, now=999) == 'retry_later'
    assert len(calls) == 1
    assert autosync.check(root, now=1000) == 'failed' and len(calls) == 2


def test_unrelated_changes_and_dry_run_never_push(pending, monkeypatch):
    root, state, calls = pending
    state['pending_since'] = 100
    assert autosync.check(root, now=700, dry_run=True) == 'due'
    assert state == {'enabled': True, 'pending_since': 100} and not calls
    monkeypatch.setattr(autosync, '_pending', lambda root: (True, True))
    assert autosync.check(root, now=700) == 'blocked' and not calls
    state['enabled'] = False
    assert autosync.check(root, now=800) == 'disabled' and not calls


def test_pending_timer_is_one_shot_and_idle_removes_it(pending, monkeypatch):
    import plistlib
    from types import SimpleNamespace
    root, state, _ = pending
    state['pending_since'] = 100
    monkeypatch.setattr(autosync.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(autosync.time, 'time', lambda: 110)
    monkeypatch.setattr(Path, 'home', lambda: root / 'home')
    commands = []
    def command(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(autosync, '_command', command)
    autosync.reconcile_timer(root)
    label = state['timer_label']
    path = autosync._timer_path(label)
    config = plistlib.loads(path.read_bytes())
    assert config['LaunchOnlyOnce'] and 'StartCalendarInterval' in config
    assert 'StartInterval' not in config and not config.get('RunAtLoad')
    autosync.reconcile_timer(root)
    assert sum('bootstrap' in args for args in commands) == 1
    assert state['pending_since'] == 100
    state['pending_since'] = None
    autosync.reconcile_timer(root)
    assert state['timer_label'] is None and not path.exists()
    assert any('bootout' in args for args in commands)


def test_change_listener_has_no_periodic_interval(pending, monkeypatch):
    import plistlib
    from types import SimpleNamespace
    root, state, _ = pending
    monkeypatch.setattr(autosync.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(Path, 'home', lambda: root / 'home')
    monkeypatch.setattr(autosync, 'gh_path', lambda: 'gh')
    def command(args, **kwargs):
        return SimpleNamespace(returncode=1 if 'print' in args else 0, stdout='true')
    monkeypatch.setattr(autosync, '_command', command)
    autosync.set_enabled(root, True)
    path = root / 'home/Library/LaunchAgents' / (autosync.LABEL + '.plist')
    config = plistlib.loads(path.read_bytes())
    assert config['WatchPaths'] and 'StartInterval' not in config and 'StartCalendarInterval' not in config
