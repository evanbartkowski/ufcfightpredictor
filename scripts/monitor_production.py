"""A small external-monitor target: fail on stale publication or source data."""
import argparse
from datetime import datetime, timezone
import json
import os
from urllib.request import Request, urlopen


def assess(payload, at=None, max_heartbeat=None, max_sync=None, max_odds=None):
    at = at or datetime.now(timezone.utc)
    max_heartbeat = max_heartbeat if max_heartbeat is not None else float(os.environ.get('MAX_HEARTBEAT_SECONDS', '2700'))
    max_sync = max_sync if max_sync is not None else float(os.environ.get('MAX_SYNC_SECONDS', '25200'))
    max_odds = max_odds if max_odds is not None else float(os.environ.get('MAX_ODDS_SECONDS', '2700'))

    def age(value):
        try:
            return (at - datetime.fromisoformat(str(value).replace('Z', '+00:00'))).total_seconds()
        except (ValueError, TypeError):
            return float('inf')

    problems = []
    if age(payload.get('published_at')) > max_heartbeat:
        unit_str = f'{max_heartbeat / 3600:.1f} hours' if max_heartbeat >= 3600 else f'{int(max_heartbeat // 60)} minutes'
        problems.append(f'No publication heartbeat in {unit_str}')
    if age(payload.get('last_sync')) > max_sync:
        unit_str = f'{max_sync / 3600:.1f} hours' if max_sync >= 3600 else f'{int(max_sync // 60)} minutes'
        problems.append(f'No successful fight-data pull in {unit_str}')
    if age(payload.get('odds_checked_at')) > max_odds:
        unit_str = f'{max_odds / 3600:.1f} hours' if max_odds >= 3600 else f'{int(max_odds // 60)} minutes'
        problems.append(f'No odds check in {unit_str}')
    if not payload.get('model_version'):
        problems.append('Serving model metadata is unavailable')
    return problems


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='https://mma-oracle.web.app')
    parser.add_argument('--max-heartbeat', type=float, default=float(os.environ.get('MAX_HEARTBEAT_SECONDS', '43200')))
    parser.add_argument('--max-sync', type=float, default=float(os.environ.get('MAX_SYNC_SECONDS', '86400')))
    parser.add_argument('--max-odds', type=float, default=float(os.environ.get('MAX_ODDS_SECONDS', '43200')))
    args = parser.parse_args()

    req = Request(args.url.rstrip('/') + '/api/health', headers={'User-Agent': 'MMA-Cipher-Monitor/1.0 (Mozilla/5.0)'})
    with urlopen(req, timeout=30) as response:
        payload = json.load(response)

    problems = assess(payload, max_heartbeat=args.max_heartbeat, max_sync=args.max_sync, max_odds=args.max_odds)
    print(json.dumps(dict(healthy=not problems, problems=problems, publication=payload.get('publication')), indent=2))
    raise SystemExit(bool(problems))
