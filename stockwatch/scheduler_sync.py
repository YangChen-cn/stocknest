"""Apply report schedules to cron-job.org without storing management credentials.

Only jobs targeting this repository's daily dispatch endpoint are considered.
Existing dispatch headers remain in memory and are preserved, never logged.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stockwatch.report_settings import PERIODIC, report_settings, report_timezone


class SchedulerError(RuntimeError):
    pass


def api(method, path, payload=None):
    key = os.environ.get('CRONJOB_API_KEY', '').strip()
    if not key:
        raise SchedulerError('CRONJOB_API_KEY is missing; external schedules were not changed.')
    request = Request('https://api.cron-job.org' + path,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except (HTTPError, URLError, OSError, ValueError):
        # Neither response bodies nor request headers may reach logs.
        raise SchedulerError('cron-job.org request failed; check the management Key, quota and job status.') from None


def apply(config, repository, *, desired='cron-job.org', request=api, sleeper=time.sleep):
    import re
    if desired not in ('native', 'cron-job.org'):
        raise SchedulerError('Invalid scheduler trigger.')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
        raise SchedulerError('Invalid repository.')
    endpoint = f'https://api.github.com/repos/{repository}/actions/workflows/daily.yml/dispatches'
    listing = request('GET', '/jobs')
    if listing.get('someFailed'):
        raise SchedulerError('Incomplete scheduler listing; no changes applied.')
    jobs = {}
    template = None
    for job in listing.get('jobs', []):
        if job.get('url') != endpoint:
            continue
        sleeper(0.25)
        detail = request('GET', f"/jobs/{job['jobId']}")['jobDetails']
        try:
            body = json.loads(detail['extendedData']['body'])
            mode = body['inputs']['mode']
            if mode not in report_settings() or body.get('ref') != 'main' or body['inputs'].get('scheduled') not in (True, 'true'):
                continue
            if body['inputs'].get('dry_run') in (True, 'true') or body['inputs'].get('sync_only') in (True, 'true'):
                continue
            headers = detail['extendedData']['headers']
            authorization = next((v for k, v in headers.items() if k.lower() == 'authorization'), '')
            if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or not authorization[7:].strip():
                raise SchedulerError('The report template lacks an authorized GitHub dispatch header.')
        except (KeyError, ValueError, TypeError):
            continue
        if mode in jobs:
            raise SchedulerError('Duplicate report jobs found; resolve them before applying schedules.')
        jobs[mode] = detail
        template = detail
    if desired == 'native':
        # Native scheduler selected: disable every StockWatch dispatch job so both
        # automatic paths never fire at once. Nothing is created.
        for mode, detail in sorted(jobs.items()):
            sleeper(0.25)
            request('PATCH', f"/jobs/{detail['jobId']}", {'job': {'enabled': False}})
            print(f'{mode}: external schedule disabled (native scheduler selected).')
        return len(jobs)
    if not template:
        raise SchedulerError('No authorized scheduled report template found. Create one in cron-job.org first.')
    plans = report_settings(config.get('reports'))
    count = 0
    for mode, plan in plans.items():
        hour, minute = map(int, plan['time'].split(':'))
        delta = {'enabled': plan['enabled'], 'saveResponses': False,
                 'title': f'StockWatch {mode} · {repository}',
                 'schedule': {'timezone': str(report_timezone(mode)), 'expiresAt': 0, 'hours': [hour], 'minutes': [minute],
                              'wdays': ([0, 6] if mode == 'MONTHLY' else [6]) if mode in PERIODIC else [day + 1 for day in plan['days']], 'months': [-1],
                              'mdays': list(range(1, 8)) if mode == 'MONTHLY' else [-1]},
                 'extendedData': {'headers': deepcopy(template['extendedData']['headers']),
                                  'body': json.dumps({'ref': 'main', 'inputs': {'mode': mode, 'scheduled': True}})}}
        if mode in jobs:
            sleeper(0.25)
            request('PATCH', f"/jobs/{jobs[mode]['jobId']}", {'job': delta})
        elif plan['enabled']:
            sleeper(1.1)
            request('PUT', '/jobs', {'job': {**delta, 'url': endpoint, 'requestMethod': 1, 'requestTimeout': 30}})
        else:
            continue
        count += 1
        print(f'{mode}: external schedule updated; enabled={plan["enabled"]}')
    return count


def main():
    import yaml
    from stockwatch.storage import validate_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.yaml'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--apply-trigger', choices=('native', 'cron-job.org'), default='cron-job.org',
                        help='Scheduler the external jobs must match; written to config on success.')
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise SchedulerError('Invalid report configuration.')
        # Validate all plans before the first mutation.
        report_settings(config.get('reports'))
        apply(config, args.repository, desired=args.apply_trigger, request=api)
        # Two-phase switch: only after the external jobs match does config move, so
        # "external selected but jobs missing" can never silence the native schedule.
        current = (config.get('scheduler') or {}).get('trigger', 'native')
        if current != args.apply_trigger:
            if args.apply_trigger == 'native':
                config.pop('scheduler', None)
            else:
                config['scheduler'] = {'trigger': args.apply_trigger}
            validate_config(config)
            args.config.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding='utf-8')
            print(f'config.yaml scheduler.trigger set to {args.apply_trigger}; commit it to main.')
    except (SchedulerError, ValueError, OSError, yaml.YAMLError) as exc:
        print(str(exc) if isinstance(exc, (SchedulerError, ValueError)) else 'Unable to read schedule configuration.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
