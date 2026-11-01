"""Verify rendered content, public API agreement, and the persisted fight state."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def verify(read, expected_commit=None):
    dashboard = json.loads(read('/api/dashboard'))
    update = json.loads(read('/api/update'))
    health = json.loads(read('/api/health'))
    model = json.loads(read('/api/model'))
    state_bytes = read('/api/sync-state.json.gz')
    state = json.loads(gzip.decompress(state_bytes))
    assert dashboard['revision'] == update['revision'] == health['revision'], 'Published revisions differ'
    assert hashlib.sha256(state_bytes).hexdigest() == health['publication']['state_sha256'], 'Saved-state digest differs'
    assert state['meta']['last_sync'] == dashboard['status']['last_sync'] == health['last_sync'], 'Source timestamps differ'
    assert len(dashboard['upcoming']) == health['upcoming_bouts'], 'Upcoming counts differ'
    assert dashboard.get('record_scope') == 'main_card' and all(r.get('card_section') == 'main' for r in dashboard['results']), 'Prediction record includes a non-main-card bout'
    scored = [r for r in dashboard['results'] if r.get('correct') is not None]
    assert len(scored) == dashboard['scored_predictions'], 'Main-card record denominator differs'
    assert dashboard['live_accuracy'] == (sum(r['correct'] for r in scored)/len(scored) if scored else None), 'Main-card record accuracy differs'
    assert all(len(state['tables'][table]) == count for table, count in health['publication']['persisted_counts'].items()), 'Persisted table counts differ'
    assert model['version'] == dashboard['model']['version'] == health['model_version'], 'Serving model versions differ'
    assert model['feature_count'] == len(model['feature_names']), 'Feature metadata is inconsistent'
    overview = json.loads(read('/api/overview'))
    portraits = json.loads(read('/api/fighter-images.json'))
    search = json.loads(read('/api/search-index.json'))
    assert {item['kind'] for item in search['items']} == {'fighters','events','forecasts','model'}, 'Search index is missing a collection'
    assert portraits == dashboard.get('images', {}), 'Published portrait index differs from the dashboard'
    assert all(url.startswith('https://a.espncdn.com/i/headshots/mma/players/full/') for url in portraits.values()), 'Portrait source is not an MMA athlete'
    assert overview['revision'] == dashboard['revision'] and overview['api_mode'] == 'static', 'Overview is not from this static publication'
    assert len(overview['fighters']) == len(dashboard['fighters']), 'Fighter search index differs'
    assert 'events_history' not in overview['model']['metrics'], 'Historical analytics leaked into the initial overview'
    assert json.loads(read('/api/fighters/page/1.json'))['per_page'] == 48
    assert json.loads(read('/api/analytics/events/page/1.json'))['per_page'] == 100
    assert json.loads(read('/api/predictions/upcoming/page/1.json'))['total'] == len(dashboard['upcoming'])
    for fight in dashboard['upcoming']:
        if fight.get('prediction_pending') or fight.get('locked'):
            continue
        assert fight['model_version'] == model['version'], 'Upcoming prediction uses a different serving model'
        assert fight['feature_names'] == model['feature_names'], 'Upcoming input schema differs from model metadata'
        assert fight['explanation'].get('method') == 'one_input_at_a_time_sensitivity', 'Upcoming forecast lacks full-model evidence'
        weights = model.get('weights') or {}
        if weights and all(name in fight.get('models', {}) for name in weights):
            weighted = sum(weight * fight['models'][name]['red_probability'] for name, weight in weights.items()) / sum(weights.values())
            # Some estimators emit float32 arrays; allow numerical rounding,
            # while rejecting a different set of blend weights or model output.
            assert abs(min(.995, max(.005, weighted)) - fight['red_probability']) < 1e-7, 'Published probability differs from the declared ensemble weights'
    if expected_commit:
        assert health['publication']['code_revision'] == expected_commit, 'Published code commit differs from the release'
        source = (ROOT / 'predictor/model.py').read_bytes()
        # Git may check out CRLF on Windows and LF on the Linux publisher.
        source_hashes = {hashlib.sha256(value).hexdigest() for value in (source, source.replace(b'\r\n', b'\n'))}
        assert model['implementation_hash'] in source_hashes, 'Published model implementation differs from the release source'
    if os.environ.get('GITHUB_RUN_ID'):
        assert health['publication']['job_url'].endswith('/' + os.environ['GITHUB_RUN_ID']), 'Publication did not come from this background job'
    html = BeautifulSoup(read('/'), 'html.parser')
    assert int(html.select_one('#card-count').get_text()) == len(dashboard['upcoming']), 'Homepage contains placeholder fight count'
    if dashboard['status']['last_sync']:
        assert dashboard['status']['last_sync'] in html.select_one('#notice').get_text(), 'Homepage source timestamp is missing'
    if dashboard['upcoming']:
        assert html.select('#cards article'), 'Homepage has no indexable fight cards'
    assert 'calibrated win probabilities' not in html.get_text().lower(), 'Unsupported calibration claim'
    assert html.select_one('link[rel="canonical"]'), 'Canonical URL is missing'
    urls = [node.text for node in ET.fromstring(read('/sitemap.xml')).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
    assert len(urls) == len(set(urls)), 'Sitemap contains duplicate URLs'
    for kind in ('fighters', 'events', 'fights', 'model', 'status'):
        candidates = [urlparse(url).path for url in urls if urlparse(url).path.startswith('/' + kind + '/')]
        assert candidates, 'Missing indexable ' + kind + ' pages'
        path = next((p for p in candidates if p.count('/') > 2), candidates[0])
        page = BeautifulSoup(read(path), 'html.parser')
        assert page.h1 and page.select_one('link[rel="canonical"]'), 'Invalid detail page: ' + path
    assert b'Sitemap:' in read('/robots.txt'), 'robots.txt does not advertise the sitemap'
    return dict(revision=dashboard['revision'], code_revision=health['publication']['code_revision'],
                last_sync=health['last_sync'], odds_checked_at=health['odds_checked_at'],
                upcoming_bouts=health['upcoming_bouts'], persisted_counts=health['publication']['persisted_counts'],
                state_sha256=health['publication']['state_sha256'], model_version=model['version'],
                feature_count=model['feature_count'], estimator_names=model['estimator_names'],
                calibration_applied=model['calibration_applied'], indexable_pages=len(urls),
                checks='API revisions, persisted state, model metadata, rendered homepage, canonical links, detail pages, sitemap, robots')


def verify_live(read, expected_commit=None, attempts=7, interval=10, pause=time.sleep):
    """Allow CDN release propagation, while requiring a fully consistent release."""
    for attempt in range(attempts):
        try:
            return verify(read, expected_commit=expected_commit)
        except (AssertionError, requests.RequestException):
            if attempt + 1 == attempts:
                raise
            print(f'Publication has not converged; retrying in {interval}s ({attempt + 1}/{attempts})',
                  file=sys.stderr, flush=True)
            pause(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', help='Verify a published origin instead of local export files')
    args = parser.parse_args()
    def read(path):
        if args.url:
            response = requests.get(args.url.rstrip('/') + path,
                                    params={'verify': os.environ.get('GITHUB_RUN_ID', 'audit'), 'probe': time.time_ns()},
                                    timeout=(15, 60))
            response.raise_for_status()
            return response.content
        target = ROOT / 'static' / path.lstrip('/')
        if path in ('/api/dashboard', '/api/update', '/api/health', '/api/model', '/api/overview'):
            target = target.with_suffix('.json')
        if path.endswith('/'):
            target /= 'index.html'
        return target.read_bytes()
    check = verify_live if args.url else verify
    result = check(read, expected_commit=os.environ.get('GITHUB_SHA'))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
