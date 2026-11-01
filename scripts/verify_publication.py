"""Verify rendered content, public API agreement, and the persisted fight state."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
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
    assert all(len(state['tables'][table]) == count for table, count in health['publication']['persisted_counts'].items()), 'Persisted table counts differ'
    assert model['version'] == dashboard['model']['version'] == health['model_version'], 'Serving model versions differ'
    assert model['feature_count'] == len(model['feature_names']), 'Feature metadata is inconsistent'
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
        assert model['implementation_hash'] == hashlib.sha256((ROOT / 'predictor/model.py').read_bytes()).hexdigest(), 'Published model implementation differs from the release source'
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', help='Verify a published origin instead of local export files')
    args = parser.parse_args()
    def read(path):
        if args.url:
            response = requests.get(args.url.rstrip('/') + path, params={'verify': os.environ.get('GITHUB_RUN_ID', 'audit')}, timeout=(15, 60))
            response.raise_for_status()
            return response.content
        target = ROOT / 'static' / path.lstrip('/')
        if path in ('/api/dashboard', '/api/update', '/api/health', '/api/model'):
            target = target.with_suffix('.json')
        if path.endswith('/'):
            target /= 'index.html'
        return target.read_bytes()
    result = verify(read, expected_commit=os.environ.get('GITHUB_SHA'))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
