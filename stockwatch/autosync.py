"""Opt-in local GitHub sync after one hour, independent of browser sessions."""
from __future__ import annotations

import json
import os
import platform
import plistlib
import sys
import time
from pathlib import Path

from stockwatch.control import ControlError, _command, gh_path, repository
from stockwatch.git_sync import EDITABLE, SyncError, _git, _sync, sync_lock
from stockwatch.storage import ValidationError, atomic_write

LABEL = "com.stockwatch.autosync"
DELAY = 3600
RETRY = 300


def _state_path(root):
    path = Path(_git(root, "rev-parse", "--git-path", "stockwatch-autosync.json").stdout.strip())
    return path if path.is_absolute() else root / path


def status(root):
    try:
        state = json.loads(_state_path(root).read_text())
        if not isinstance(state, dict) or not isinstance(state.get('enabled'), bool):
            raise ValueError
        for key in ('pending_since', 'last_attempt', 'last_success'):
            value = state.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float('inf')):
                raise ValueError
        return {'enabled': state['enabled'], 'pending_since': state.get('pending_since'),
                'last_attempt': state.get('last_attempt'), 'last_success': state.get('last_success'),
                'last_error': state.get('last_error') if state.get('last_error') in ('blocked_changes', 'sync_failed') else None}
    except (OSError, ValueError, TypeError):
        return {'enabled': False, 'pending_since': None}


def _save(root, state):
    path = _state_path(root)
    atomic_write(path, json.dumps(state, sort_keys=True) + '\n')
    path.chmod(0o600)


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
        state.update(pending_since=None, last_error=None, last_success=now)
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
                    'WorkingDirectory': str(root), 'RunAtLoad': True, 'StartInterval': 60,
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
    if enabled and not loaded:
        try:
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


def main(argv=None):
    import argparse
    from stockwatch.storage import ROOT
    parser = argparse.ArgumentParser(description='Local delayed GitHub sync')
    parser.add_argument('action', choices=('check', 'status', 'enable', 'disable'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'status':
            print(json.dumps(status(args.root)))
        elif args.action == 'check':
            print(check(args.root, dry_run=args.dry_run))
        else:
            set_enabled(args.root, args.action == 'enable')
            print(args.action)
        return 0
    except (SyncError, ControlError, ValidationError, OSError):
        print('Automatic sync unavailable; local changes preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
