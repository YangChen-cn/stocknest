"""Optional, restricted cloud watchlist editing through GitHub Contents API.

Credentials are read only from environment/Streamlit Secrets. Writes are fixed
at config.yaml on main in a verified private repository, never arbitrary paths.
"""
import base64
from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from stockwatch.storage import ValidationError, validate_config


class CloudConfigError(ValidationError):
    pass


@dataclass(frozen=True)
class CloudSettings:
    repository: str
    token: str = field(repr=False)

    @classmethod
    def from_environment(cls):
        repo = os.environ.get('STOCKWATCH_CLOUD_REPOSITORY', '').strip()
        token = os.environ.get('STOCKWATCH_CLOUD_TOKEN', '').strip()
        if not repo or not token:
            raise CloudConfigError('Cloud editing is not configured. Add the dedicated repository and token to Streamlit Secrets.')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise CloudConfigError('Cloud repository must be owner/repository.')
        if not re.fullmatch(r"[A-Za-z0-9_]+", token):
            raise CloudConfigError("Cloud token has an invalid format. Check Streamlit Secrets.")
        return cls(repo, token)


def configured():
    return bool(os.environ.get('STOCKWATCH_CLOUD_REPOSITORY', '').strip() and
                os.environ.get('STOCKWATCH_CLOUD_TOKEN', '').strip())


MISSING = object()


def merge_watchlist(base, desired, current):
    """Merge only changed fields; same-field conflicts never overwrite remote data."""
    def merge(old, new, latest, path):
        result = deepcopy(latest)
        for key in sorted(old.keys() | new.keys()):
            before, after, remote = old.get(key, MISSING), new.get(key, MISSING), latest.get(key, MISSING)
            if before == after:
                continue
            field_path = f'{path}/{key}' if path else key
            if remote == after:
                continue  # Already applied by another session.
            if before is not MISSING and isinstance(before, dict) and isinstance(after, dict) and isinstance(remote, dict):
                result[key] = merge(before, after, remote, field_path)
            elif remote == before:
                if after is MISSING:
                    result.pop(key, None)
                else:
                    result[key] = deepcopy(after)
            else:
                raise CloudConfigError('Watchlist changed elsewhere: {field}. Your draft was not saved. Reload the latest configuration before editing again.', field=field_path)
        return result
    if not all(isinstance(value, dict) for value in (base, desired, current)):
        raise CloudConfigError('Invalid watchlist edit.')
    return merge(base, desired, current, '')


class CloudRepository:
    def __init__(self, settings, *, opener=urlopen):
        self.settings = settings
        self.opener = opener

    def _request(self, method, path, body=None):
        # Never use credentials in URLs; raw responses/errors are not logged.
        try:
            request = Request(
                f'https://api.github.com/repos/{self.settings.repository}' + (f'/{path}' if path else ''),
                method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Accept': 'application/vnd.github+json',
                         'Authorization': f'Bearer {self.settings.token}',
                         'X-GitHub-Api-Version': '2022-11-28',
                         'Content-Type': 'application/json',
                         'User-Agent': 'StockWatch-cloud-config'},
            )
            with self.opener(request, timeout=15) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code == 409:
                raise CloudConfigError('GitHub configuration changed during saving. Nothing was overwritten; refresh the configuration and retry.') from None
            if exc.code in (401, 403, 404):
                raise CloudConfigError(
                    'Cloud GitHub access failed (HTTP {status}, {resource}). Check the dedicated token, its expiry, repository selection and Contents permission.',
                    status=exc.code, resource='config.yaml' if path else 'repository',
                ) from None
            raise CloudConfigError('Cloud save could not be confirmed. Refresh the latest configuration before retrying.') from None
        except (OSError, URLError, ValueError, TimeoutError):
            raise CloudConfigError('Cloud request failed or timed out. If saving, refresh the latest configuration to check whether it succeeded.') from None

    def read_config(self):
        repo = self._request('GET', '')
        if not isinstance(repo, dict) or repo.get('private') is not True or repo.get('default_branch') != 'main':
            raise CloudConfigError('Cloud editing requires a verified private repository with default branch main.')
        content = self._request('GET', 'contents/config.yaml?ref=main')
        try:
            if content['type'] != 'file' or content['encoding'] != 'base64':
                raise ValueError
            raw = yaml.safe_load(base64.b64decode(content['content']).decode('utf-8'))
            clean = validate_config(raw)
            sha = content['sha']
            if not re.fullmatch(r'[0-9a-f]{40,64}', sha):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeError, yaml.YAMLError, ValidationError):
            raise CloudConfigError('GitHub configuration is invalid. No cloud changes were written.') from None
        return {'config': clean, 'raw': raw, 'sha': sha}

    def save_scheduler_trigger(self, base_trigger, desired_trigger):
        """Restricted single-field write: the scheduler trigger, nothing else."""
        if desired_trigger not in ("native", "cron-job.org"):
            raise CloudConfigError('scheduler.trigger must be native or cron-job.org.')
        latest = self.read_config()
        current = latest['config'].get('scheduler', {}).get('trigger', 'native')
        if desired_trigger == current:
            return {'config': latest['config'], 'changed': False}
        if base_trigger != current:
            raise CloudConfigError('Schedule trigger changed elsewhere. Reload the latest configuration before saving.')
        raw = deepcopy(latest['raw'])
        if desired_trigger == 'native':
            raw.pop('scheduler', None)
        else:
            raw['scheduler'] = {'trigger': desired_trigger}
        clean = validate_config(raw)
        text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self._request('PUT', 'contents/config.yaml', {'branch': 'main', 'sha': latest['sha'],
                      'message': 'chore: update StockWatch schedule trigger from dashboard [skip ci]',
                      'content': base64.b64encode(text.encode('utf-8')).decode('ascii')})
        return {'config': clean, 'changed': True}

    def save_watchlist(self, base, desired):
        # Reject malformed data before making any write. No non-watchlist input
        # is accepted by this interface, including transaction or report settings.
        desired = validate_config({'watchlist': desired})['watchlist']
        base = validate_config({'watchlist': base})['watchlist']
        latest = self.read_config()
        merged = merge_watchlist(base, desired, latest['config']['watchlist'])
        if merged == latest['config']['watchlist']:
            return {'config': latest['config'], 'changed': False}
        raw = deepcopy(latest['raw'])
        raw['watchlist'] = merged
        clean = validate_config(raw)
        text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self._request('PUT', 'contents/config.yaml', {'branch': 'main', 'sha': latest['sha'],
                      'message': 'chore: update StockWatch watchlist from dashboard [skip ci]',
                      'content': base64.b64encode(text.encode('utf-8')).decode('ascii')})
        return {'config': clean, 'changed': True}
