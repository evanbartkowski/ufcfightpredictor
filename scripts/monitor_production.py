"""A small external-monitor target: fail on stale publication or source data."""
import argparse
from datetime import datetime,timezone
import json
from urllib.request import urlopen


def assess(payload, at=None):
    at=at or datetime.now(timezone.utc)
    def age(value):
        try:return (at-datetime.fromisoformat(str(value).replace('Z','+00:00'))).total_seconds()
        except (ValueError,TypeError):return float('inf')
    problems=[]
    if age(payload.get('published_at'))>2700:problems.append('No publication heartbeat in 45 minutes')
    if age(payload.get('last_sync'))>25200:problems.append('No successful fight-data pull in seven hours')
    if age(payload.get('odds_checked_at'))>2700:problems.append('No odds check in 45 minutes')
    if not payload.get('model_version'):problems.append('Serving model metadata is unavailable')
    return problems


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='https://mma-oracle.web.app');args=parser.parse_args()
    with urlopen(args.url.rstrip('/')+'/api/health',timeout=30) as response:payload=json.load(response)
    problems=assess(payload)
    print(json.dumps(dict(healthy=not problems,problems=problems,publication=payload.get('publication')),indent=2))
    raise SystemExit(bool(problems))
