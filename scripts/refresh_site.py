"""Fetch verified data and prepare a new atomic Firebase Hosting publication."""
import argparse
import logging
from pathlib import Path
import sys
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from predictor.publication import download_state, update_publication
from predictor.service import Service
from predictor.store import Store


def build_static_page():
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=select_autoescape(['html']))
    def static_url(endpoint, filename, **query):
        if endpoint != 'static':
            raise ValueError('Only static assets belong in the Hosting export')
        return '/' + filename + ('?' + urlencode(query) if query else '')
    html = env.get_template('index.html').render(url_for=static_url)
    (ROOT / 'static' / 'index.html').write_text(html, encoding='utf-8')


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
    build_static_page()
    for failure in failures:
        logging.error('%s', failure)
    logging.info('Publication ready: %s', store.meta('last_sync'))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
