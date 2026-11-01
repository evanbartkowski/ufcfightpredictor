"""Portable public fight state for unattended Firebase Hosting updates.

Hosting publishes the dashboard and its next updater's state in one version.
Only public fight data is exported; no credentials, local settings, or watched
fighter activity are included. Model binaries stay in the runner's cache.
"""
import gzip
import json
import os
import hashlib
import subprocess
import copy
from pathlib import Path

import requests

from .store import identity, now
from .api_views import export_api

TABLES = ('fights', 'fighters', 'predictions', 'latest_predictions', 'prediction_revisions')
META = ('upcoming', 'markets', 'last_sync', 'sync_error', 'profile_errors',
        'odds_feed_failed', 'odds_checked_at', 'odds_last_sync', 'odds_error',
        'training_pending', 'source_parser_version', 'seeded', 'seeded_schema')
META += ('pre_fight_markets',)


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(content)
    os.replace(temp, path)


def export_state(store):
    # Read one consistent database snapshot, including immutable predictions.
    with store.connect() as db:
        db.execute('BEGIN')
        tables = {table: [json.loads(row[0]) for row in db.execute(f'SELECT payload FROM {table}')]
                  for table in TABLES}
        meta = {key: json.loads(value) for key, value in db.execute('SELECT key,payload FROM meta') if key in META}
    payload = dict(schema=1, exported_at=now(), tables=tables, meta=meta)
    return gzip.compress(json.dumps(payload, separators=(',', ':')).encode(), mtime=0)


def restore_state(store, content):
    payload = json.loads(gzip.decompress(content))
    if payload.get('schema') != 1 or not all(isinstance(payload.get('tables', {}).get(t), list) for t in TABLES):
        raise ValueError('Unrecognized published fight state; refusing to replace saved data.')
    # A failed import rolls back as a unit. Table names and meta keys are local
    # constants, never SQL taken from the remote file.
    with store.connect() as db:
        for table in TABLES:
            db.execute(f'DELETE FROM {table}')
            for row in payload['tables'][table]:
                text = json.dumps(row)
                if table == 'fighters':
                    db.execute('INSERT INTO fighters VALUES (?,?)', (identity(row['name']), text))
                elif table == 'prediction_revisions':
                    db.execute('INSERT INTO prediction_revisions VALUES (?,?,?)', (row['id'], row['revision'], text))
                else:
                    db.execute(f'INSERT INTO {table} VALUES (?,?,?)', (row['id'], row['date'], text))
        for key in META:
            db.execute('DELETE FROM meta WHERE key=?', (key,))
            if key in payload.get('meta', {}):
                db.execute('INSERT INTO meta VALUES (?,?)', (key, json.dumps(payload['meta'][key])))


def download_state(store, url):
    response = requests.get(url, params={'update': now()}, timeout=(15, 90))
    response.raise_for_status()
    restore_state(store, response.content)


def update_publication(service, directory, force=False):
    """Refresh due feeds and export even failures with honest freshness labels."""
    failures = []
    try:
        service.sync_odds()
    except Exception as exc:
        failures.append('Odds: ' + str(exc))
        service.store.set_meta('odds_feed_failed', True)
        service.store.set_meta('odds_error', 'ESPN refresh failed; cached odds may be stale.')
    if force or service.sync_due():
        try:
            service.sync()
        except Exception as exc:
            failures.append('Fight data: ' + str(exc))
            service.store.set_meta('sync_error', dict(message=str(exc), at=now()))
    # Update eligible forecasts with the actual loaded bundle and explanation
    # implementation. Original issued forecasts and started events stay frozen.
    if service.bundle is not None:
        service.refresh_predictions(service.store.meta('upcoming', []))
    state = export_state(service.store)
    dashboard = copy.deepcopy(service.dashboard())
    dashboard['published_at'] = now()
    dashboard['revision'] = dashboard['published_at']
    dashboard['refresh'].update(automatic=True, delivery='scheduled', retry_seconds=900)
    dashboard['status'].update(running=False, phase='Scheduled updates active')
    commit = os.environ.get('GITHUB_SHA')
    if not commit:
        try:
            commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = 'unavailable'
    run_id = os.environ.get('GITHUB_RUN_ID')
    with service.store.connect() as db:
        counts = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES}
    dashboard['publication'] = dict(code_revision=commit, state_sha256=hashlib.sha256(state).hexdigest(),
                                    job_event=os.environ.get('GITHUB_EVENT_NAME','local'),
                                    persisted_counts=counts, hosting='Firebase Hosting static export',
                                    job_url=f'https://github.com/evanbartkowski/ufcfightpredictor/actions/runs/{run_id}' if run_id else None)
    directory = Path(directory)
    atomic_write(directory / 'dashboard.json', json.dumps(dashboard, separators=(',', ':')).encode())
    atomic_write(directory / 'sync-state.json.gz', state)
    atomic_write(directory / 'update.json', json.dumps(dict(revision=dashboard['revision'])).encode())
    atomic_write(directory / 'model.json', json.dumps(dashboard['model']).encode())
    export_api(directory,dashboard,service.bundle,atomic_write)
    atomic_write(directory / 'health.json', json.dumps(dict(
        revision=dashboard['revision'], published_at=dashboard['published_at'],
        last_sync=dashboard['status']['last_sync'], last_error=dashboard['status']['last_error'],
        odds_checked_at=dashboard['odds_status']['checked_at'], odds_error=dashboard['odds_status']['error'],
        upcoming_bouts=len(dashboard['upcoming']), verified_bouts=dashboard['total_fights'],
        model_version=dashboard['model']['version'] if dashboard['model'] else None,
        refresh=dashboard['refresh'],
        publication=dashboard['publication'])).encode())
    return failures
