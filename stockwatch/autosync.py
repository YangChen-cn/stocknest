"""Event-driven local GitHub sync with one-shot delayed jobs, never idle polling."""
from __future__ import annotations

import json
import math
import os
import platform
import plistlib
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from stockwatch.control import ControlError, _command, gh_path, repository
from stockwatch.git_sync import EDITABLE, SyncError, _git, _sync, sync_lock
from stockwatch.storage import ValidationError, atomic_write

LABEL = "com.stockwatch.autosync"
DELAY = 600
RETRY = 300
TIMER_PREFIX = LABEL + '.pending.'


def _timer_label(value):
    return isinstance(value, str) and re.fullmatch(re.escape(TIMER_PREFIX) + r'[0-9a-f]{32}', value) is not None


def _state_path(root):
    path = Path(_git(root, "rev-parse", "--git-path", "stockwatch-autosync.json").stdout.strip())
    return path if path.is_absolute() else root / path


def status(root):
    try:
        state = json.loads(_state_path(root).read_text())
        if not isinstance(state, dict) or not isinstance(state.get('enabled'), bool):
            raise ValueError
        for key in ('pending_since', 'last_attempt', 'last_success', 'scheduled_for'):
            value = state.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float('inf')):
                raise ValueError
        return {'enabled': state['enabled'], 'pending_since': state.get('pending_since'),
                'last_attempt': state.get('last_attempt'), 'last_success': state.get('last_success'),
                'last_error': state.get('last_error') if state.get('last_error') in ('blocked_changes', 'sync_failed', 'schedule_failed') else None,
                'timer_label': state.get('timer_label') if _timer_label(state.get('timer_label')) else None,
                'scheduled_for': state.get('scheduled_for')}
    except (OSError, ValueError, TypeError):
        return {'enabled': False, 'pending_since': None}


def _save(root, state):
    path = _state_path(root)
    atomic_write(path, json.dumps(state, sort_keys=True) + '\n')
    path.chmod(0o600)


def _timer_path(label):
    if not _timer_label(label):
        raise ControlError('Invalid automatic sync timer.')
    return Path.home() / 'Library/LaunchAgents' / f'{label}.plist'


def _remove_timer(root, label):
    if not label:
        return
    path = _timer_path(label)
    if not path.exists():
        return
    try:
        data = plistlib.loads(path.read_bytes())
        if data.get('Label') != label or data.get('WorkingDirectory') != str(root.resolve()):
            raise ValueError
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise ControlError('The automatic sync service belongs to another checkout or is invalid.') from None
    path.unlink()
    # When called by the timer itself this is deliberately the final operation:
    # result/state and any next retry have already been saved and registered.
    _command(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], check=False)


def reconcile_timer(root, *, current_label=None):
    """Only register a one-shot calendar job while a saved edit is pending."""
    if platform.system() != 'Darwin':
        return
    old_label = None
    with sync_lock(root):
        state = status(root)
        old_label = state.get('timer_label')
        if not state['enabled'] or state.get('pending_since') is None:
            state.update(timer_label=None, scheduled_for=None)
            _save(root, state)
        else:
            now = time.time()
            due = state['pending_since'] + DELAY
            if state.get('last_attempt') is not None:
                due = max(due, state['last_attempt'] + RETRY)
            if state.get('last_error') == 'blocked_changes':
                due = max(due, now + RETRY)
            target = f'gui/{os.getuid()}'
            if old_label and old_label != current_label and (state.get('scheduled_for') or 0) >= due and _command(['launchctl', 'print', f'{target}/{old_label}'], check=False).returncode == 0:
                return
            due = math.ceil(max(now + 1, due) / 60) * 60
            label = TIMER_PREFIX + uuid.uuid4().hex
            moment = datetime.fromtimestamp(due)
            path = _timer_path(label)
            data = {'Label': label, 'ProgramArguments': [sys.executable, '-m', 'stockwatch.autosync', 'check', '--root', str(root.resolve()), '--timer-label', label],
                    'WorkingDirectory': str(root.resolve()), 'LaunchOnlyOnce': True,
                    'StartCalendarInterval': {'Month': moment.month, 'Day': moment.day, 'Hour': moment.hour, 'Minute': moment.minute},
                    'StandardOutPath': str(root / 'logs/autosync.stdout.log'), 'StandardErrorPath': str(root / 'logs/autosync.stderr.log')}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            try:
                temporary.write_bytes(plistlib.dumps(data))
                temporary.replace(path)
                _command(['launchctl', 'bootstrap', target, str(path)])
            except (ControlError, OSError):
                path.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            state.update(timer_label=label, scheduled_for=due)
            _save(root, state)
    _remove_timer(root, old_label)


def request(root):
    """Called after an application save; no network and no periodic polling."""
    if platform.system() != 'Darwin' or not (root / '.git').exists():
        return
    try:
        with sync_lock(root):
            state = status(root)
            if not state['enabled']:
                return
            pending, _ = _pending(root)
            if pending and state.get('pending_since') is None:
                state['pending_since'] = time.time()
            elif not pending:
                state.update(pending_since=None, last_error=None)
            _save(root, state)
        reconcile_timer(root)
    except (SyncError, ControlError, OSError):
        # The user's validated save must survive a scheduling failure or busy Git.
        pass


def _pending(root):
    entries = [item for item in _git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').stdout.split('\0') if item]
    editable = any(item[3:] in EDITABLE for item in entries)
    blocked = any(item[3:] not in EDITABLE or item[:2] not in {' M', 'M ', 'MM', '??', 'A ', 'AM'} for item in entries)
    ahead = _git(root, 'log', '--format=', '--name-only', 'origin/main..HEAD').stdout.splitlines()
    names = {name for name in ahead if name}
    editable = editable or bool(names & EDITABLE)
    blocked = blocked or bool(names - EDITABLE) or _git(root, 'branch', '--show-current').stdout.strip() != 'main'
    return editable, blocked


def check(root, *, now=None, dry_run=False):
    """No quotes, emails or credentials are read; sync performs privacy checks."""
    now = time.time() if now is None else now
    with sync_lock(root):
        state = status(root)
        if not state['enabled']:
            return 'disabled'
        pending, blocked = _pending(root)
        if not pending:
            state.update(pending_since=None, last_error=None)
            if not dry_run:
                _save(root, state)
            return 'idle'
        if state.get('pending_since') is None:
            state['pending_since'] = now
        if now - state['pending_since'] < DELAY:
            if not dry_run:
                _save(root, state)
            return 'pending'
        if blocked:
            state['last_error'] = 'blocked_changes'
            if not dry_run:
                _save(root, state)
            return 'blocked'
        last_attempt = state.get('last_attempt')
        if last_attempt is not None and now - last_attempt < RETRY:
            return 'retry_later'
        if dry_run:
            return 'due'
        state['last_attempt'] = now
        try:
            repository(root)  # Automatic sync is restricted to GitHub origins.
            _sync(root)       # Reuses validation, private repo and whitelist guards.
        except (SyncError, ControlError, ValidationError, OSError):
            state['last_error'] = 'sync_failed'
            _save(root, state)
            return 'failed'
        remaining, _ = _pending(root)
        state.update(pending_since=now if remaining else None, last_error=None, last_success=now)
        _save(root, state)
        return 'synced'


def set_enabled(root, enabled):
    if platform.system() != 'Darwin':
        raise ControlError('Automatic GitHub sync is available only on macOS.')
    root = root.resolve()
    path = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    target = f'gui/{os.getuid()}/{LABEL}'
    if path.exists():
        try:
            existing = plistlib.loads(path.read_bytes())
            if existing.get('Label') != LABEL or existing.get('WorkingDirectory') != str(root):
                raise ValueError
        except (ValueError, OSError, plistlib.InvalidFileException):
            raise ControlError('The automatic sync service belongs to another checkout or is invalid.') from None
    with sync_lock(root):
        if enabled:
            repo = repository(root)
            if _command([gh_path(), 'api', f'repos/{repo}', '--jq', '.private'], root=root).stdout.strip() != 'true':
                raise SyncError('Personal configuration can only sync to a verified private GitHub repository.')
            logs = root / 'logs'
            logs.mkdir(exist_ok=True)
            data = {'Label': LABEL, 'ProgramArguments': [sys.executable, '-m', 'stockwatch.autosync', 'check', '--root', str(root)],
                    'WorkingDirectory': str(root), 'RunAtLoad': True,
                    'WatchPaths': [str(root / name) for name in sorted(EDITABLE)],
                    'StandardOutPath': str(logs / 'autosync.stdout.log'), 'StandardErrorPath': str(logs / 'autosync.stderr.log')}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            try:
                temporary.write_bytes(plistlib.dumps(data))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        state = status(root)
        state['enabled'] = bool(enabled)
        _save(root, state)
    loaded = _command(['launchctl', 'print', target], check=False).returncode == 0
    if enabled:
        try:
            if loaded:
                _command(['launchctl', 'bootout', target])
                for _ in range(100):
                    if _command(['launchctl', 'print', target], check=False).returncode:
                        break
                    time.sleep(0.05)
                else:
                    raise ControlError('Control command failed. Check GitHub login, permissions or launchd in a terminal.')
            _command(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)])
        except ControlError:
            with sync_lock(root):
                state = status(root)
                state['enabled'] = False
                _save(root, state)
            raise
    elif not enabled:
        if loaded:
            _command(['launchctl', 'bootout', target])
        path.unlink(missing_ok=True)
        reconcile_timer(root)


def main(argv=None):
    import argparse
    from stockwatch.storage import ROOT
    parser = argparse.ArgumentParser(description='Local delayed GitHub sync')
    parser.add_argument('action', choices=('check', 'status', 'enable', 'disable'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--timer-label')
    args = parser.parse_args(argv)
    if args.timer_label and not _timer_label(args.timer_label):
        parser.error('Invalid timer label')
    try:
        if args.action == 'status':
            print(json.dumps(status(args.root)))
        elif args.action == 'check':
            print(check(args.root, dry_run=args.dry_run), flush=True)
            if not args.dry_run:
                reconcile_timer(args.root, current_label=args.timer_label)
                _remove_timer(args.root, args.timer_label)
        else:
            set_enabled(args.root, args.action == 'enable')
            print(args.action)
        return 0
    except (SyncError, ControlError, ValidationError, OSError):
        print('Automatic sync unavailable; local changes preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
