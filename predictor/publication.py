"""Portable public fight state for unattended Firebase Hosting updates.

Hosting publishes the dashboard and its next updater's state in one version.
Only public fight data is exported; no credentials, local settings, or watched
fighter activity are included. Model binaries stay in the runner's cache.
"""
import gzip
import json
import os
from pathlib import Path

import requests

from .store import identity, now

TABLES = ('fights', 'fighters', 'predictions', 'latest_predictions', 'prediction_revisions')
META = ('upcoming', 'markets', 'last_sync', 'sync_error', 'profile_errors',
        'odds_feed_failed', 'odds_checked_at', 'odds_last_sync', 'odds_error',
        'training_pending', 'source_parser_version', 'seeded', 'seeded_schema')


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
    dashboard = service.dashboard()
    dashboard['published_at'] = now()
    dashboard['revision'] = dashboard['published_at']
    dashboard['refresh'].update(automatic=True, delivery='scheduled', retry_seconds=900)
    dashboard['status'].update(running=False, phase='Scheduled updates active')
    directory = Path(directory)
    atomic_write(directory / 'dashboard.json', json.dumps(dashboard, separators=(',', ':')).encode())
    atomic_write(directory / 'sync-state.json.gz', export_state(service.store))
    atomic_write(directory / 'update.json', json.dumps(dict(revision=dashboard['revision'])).encode())
    return failures
