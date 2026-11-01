"""Fetch verified data and prepare a new atomic Firebase Hosting publication."""
import argparse
import logging
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from predictor.publication import download_state, update_publication
from predictor.service import Service
from predictor.store import Store
from predictor.rendering import export_pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore-url', help='Restore public data from the last successful Hosting publication')
    parser.add_argument('--force', action='store_true', help='Refresh fight data even if the six-hour interval has not elapsed')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = Store(args.data_dir)
    if args.restore_url:
        # An unavailable state is a hard failure: never deploy an empty reset.
        download_state(store, args.restore_url)
    store.bootstrap(ROOT)
    service = Service(store)
    latest_result = max((f['date'] for f in store.rows('fights')), default=None)
    if service.bundle is None or store.meta('training_pending') or service.bundle.get('through') != latest_result:
        service.retrain()
    failures = update_publication(service, ROOT / 'static' / 'api', force=args.force)
    dashboard = json.loads((ROOT / 'static' / 'api' / 'dashboard.json').read_text(encoding='utf-8'))
    page_count = export_pages(service, dashboard=dashboard)
    logging.info('Rendered %s indexable pages, sitemap, and robots.txt', page_count)
    for failure in failures:
        logging.error('%s', failure)
    logging.info('Publication ready: %s', store.meta('last_sync'))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
