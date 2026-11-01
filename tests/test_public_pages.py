from datetime import datetime, timezone
import gzip
import json

from bs4 import BeautifulSoup
import numpy as np
import pytest

from app import create_app
from predictor.catalog import Catalog
from predictor.links import event_path, fight_path, fighter_path
from predictor.model import FEATURES, IMPLEMENTATION_HASH, SCHEMA, fresh, predict
from predictor.model_metadata import describe_model
from predictor.publication import update_publication
from predictor.rendering import export_pages
from predictor.store import bout_id, identity
from scripts.verify_publication import verify, verify_live


class Estimator:
    n_features_in_ = len(FEATURES)

    def __init__(self, index, weight):
        self.index, self.weight = index, weight

    def predict_proba(self, values):
        p = 1 / (1 + np.exp(-np.asarray(values)[:, self.index] * self.weight))
        return np.column_stack([1 - p, p])


def serving_bundle():
    tree = Estimator(0, .01)
    linear = Estimator(18, -.1)
    return dict(model=tree, models={'Tree estimator': tree, 'MMA Cipher Logistic Regression': linear},
                weights={'Tree estimator': .9, 'MMA Cipher Logistic Regression': .1},
                states={identity('Alice'): dict(fresh(), elo=1700, wins=10, reach=190),
                        identity('Beth'): dict(fresh(), elo=1500, wins=5, reach=175)},
                schema=SCHEMA, implementation_hash=IMPLEMENTATION_HASH,
                version='test-version', name='Test ensemble', through='2020-02-01',
                metrics=dict(training_fights=15, test_fights=3, test_start='2020-01-01', test_end='2020-02-01',
                             accuracy=.6, brier=.24, log_loss=.66, elo_accuracy=.5, calibration=[]))


@pytest.fixture
def public_app(tmp_path):
    app = create_app(tmp_path, bootstrap=False, auto_updates=False)
    service = app.extensions['predictor']
    service.bundle = serving_bundle()
    for name in ('Alice', 'Beth'):
        service.store.save_fighter(dict(name=name, record='10-3-0', SLpM='4.2', td_avg='1.5', source='UFCStats', fetched_at='2026-11-01'))
    for day, winner in [('2020-01-01', 'red'), ('2020-02-01', 'blue')]:
        service.store.save_fight(dict(id=bout_id(day, 'Alice', 'Beth'), date=day, event='Test UFC', red='Alice', blue='Beth', winner=winner, method='KO/TKO'))
    f = dict(id=bout_id('2099-01-01', 'Alice', 'Beth'), date='2099-01-01', event='Future UFC',
             red='Alice', blue='Beth', winner=None, weight_class='Lightweight')
    service.store.set_meta('upcoming', [f])
    service.store.set_meta('last_sync', datetime.now(timezone.utc).isoformat())
    service.refresh_predictions([f])
    return app


def test_model_metadata_uses_fitted_estimators_and_feature_dimension():
    metadata = describe_model(serving_bundle())
    assert metadata['feature_count'] == len(FEATURES)
    assert metadata['estimator_names'] == ['Tree estimator', 'MMA Cipher Logistic Regression']
    assert not metadata['calibration_applied']
    assert 'not been separately calibrated' in metadata['calibration_note']
    assert 'Nelder' not in metadata['weight_selection']


def test_explanation_tracks_the_weighted_ensemble_instead_of_logistic_component():
    p = predict(serving_bundle(), 'Alice', 'Beth', '2026-11-07')
    exp = p['explanation']
    assert exp['advantages'][0]['feature'] == 'Elo'
    assert exp['advantages'][0]['effect_points'] > 30
    assert exp['risks'][0]['feature'] == 'Reach differential'
    assert exp['causal'] is False and exp['scope'] == 'full serving ensemble'
    assert exp['support']['red_bouts'] == 10 and exp['support']['blue_bouts'] == 5
    reverse = predict(serving_bundle(), 'Beth', 'Alice', '2026-11-07')
    assert reverse['pick'] == p['pick']
    assert reverse['explanation']['advantages'][0]['effect_points'] == pytest.approx(exp['advantages'][0]['effect_points'])


def test_sparse_history_is_reported_as_major_uncertainty():
    p = predict(serving_bundle(), 'New fighter', 'Beth', '2026-11-07')
    assert p['explanation']['support']['red_bouts'] == 0
    assert 'fewer than three' in p['explanation']['major_uncertainty']
    assert p['consensus']['uncertainty'] == 'Elevated'


def test_archive_search_index_covers_every_collection_and_fighters_beyond_page_one(public_app):
    service = public_app.extensions['predictor']
    for index in range(55):
        service.store.save_fighter(dict(name=f'Roster fighter {index:02d}'))
    payload = public_app.test_client().get('/api/search-index.json').json
    items = payload['items']
    assert {item['kind'] for item in items} == {'fighters','events','forecasts','model'}
    assert any(item['title'] == 'Roster fighter 54' for item in items)
    assert any(item['kind'] == 'events' and 'Alice' in item['terms'] and 'Beth' in item['terms'] for item in items)
    paths = [item['path'] for item in items]
    assert len(paths) == len(set(paths))
    assert any(item['kind'] == 'forecasts' and item['title'] == 'Alice vs. Beth' for item in items)


def test_raw_homepage_contains_real_data_and_crawlable_forecast(public_app):
    response = public_app.test_client().get('/')
    assert response.status_code == 200
    html = BeautifulSoup(response.data, 'html.parser')
    assert html.select_one('#card-count').get_text() == '1'
    assert 'Not yet' not in html.select_one('#last-sync').get_text()
    assert 'Future UFC' in html.select_one('#cards').get_text()
    assert html.select_one('#cards a[href^="/fights/"]')
    assert f'{len(FEATURES)} pre-bout inputs' in html.select_one('#model-summary').get_text()
    assert 'Calibrated win probabilities' not in html.get_text()


def test_fighter_history_career_snapshot_and_elo_are_distinct(public_app):
    catalog = Catalog(public_app.extensions['predictor'])
    context = catalog.fighter(fighter_path('Alice'))
    assert context['wins'] == context['losses'] == 1
    assert context['profile']['record'] == '10-3-0'
    assert context['elo_points'][0]['before'] == 1500
    assert context['elo_points'][0]['after'] == 1515
    page = public_app.test_client().get(fighter_path('Alice'))
    assert page.status_code == 200
    assert b'Elo progression' in page.data and b'UFC opponent history' in page.data
    assert b'Striking and grappling' in page.data


def test_detail_routes_and_unknown_pages(public_app):
    catalog = Catalog(public_app.extensions['predictor'])
    client = public_app.test_client()
    for path in catalog.paths():
        page = client.get(path)
        assert page.status_code == 200, path
        assert BeautifulSoup(page.data, 'html.parser').select_one('link[rel="canonical"]')
    assert client.get('/fighters/not-a-recorded-fighter/').status_code == 404
    assert client.get('/sitemap.xml').status_code == 200
    assert b'Sitemap:' in client.get('/robots.txt').data


def test_profile_name_spacing_variants_share_one_canonical_path(public_app):
    service = public_app.extensions['predictor']
    service.store.save_fighter(dict(name='Al ice', record='10-3-0'))
    catalog = Catalog(service)
    assert catalog.fighter_url('Alice') == fighter_path('Al ice')
    assert fighter_path('Al ice') in catalog.paths()
    for event in catalog.events.values():
        for fight in event['fights']:
            assert fight['red_path'] in catalog.paths()
            assert fight['blue_path'] in catalog.paths()


def test_announced_bout_without_an_issued_forecast_has_a_readable_page(public_app):
    service = public_app.extensions['predictor']
    with service.store.connect() as db:
        db.execute('DELETE FROM predictions')
        db.execute('DELETE FROM latest_predictions')
    fight = service.store.meta('upcoming')[0]
    response = public_app.test_client().get(fight_path(fight))
    assert response.status_code == 200
    assert b'No eligible pre-event forecast has been issued' in response.data


def test_exported_pages_and_state_verify_together(public_app, tmp_path, monkeypatch):
    service = public_app.extensions['predictor']
    monkeypatch.setattr(service, 'sync_odds', lambda: None)
    root = tmp_path / 'public'
    assert update_publication(service, root / 'api') == []
    dashboard = json.loads((root / 'api/dashboard.json').read_text())
    count = export_pages(service, root, dashboard)
    def read(path):
        file = root / path.lstrip('/')
        if path.startswith('/api/') and '.' not in file.name:
            file = file.with_suffix('.json')
        if path.endswith('/'):
            file /= 'index.html'
        return file.read_bytes()
    verified = verify(read)
    assert verified['indexable_pages'] == count
    assert verified['feature_count'] == len(FEATURES)
    state = json.loads(gzip.decompress(read('/api/sync-state.json.gz')))
    assert len(state['tables']['predictions']) == 1
    # Hosting can briefly return an older health object immediately after release.
    # Retry the complete audit, then require agreement rather than accepting it.
    probes = []
    def propagating_read(path):
        data = read(path)
        if path == '/api/health':
            health = json.loads(data)
            probes.append(path)
            if len(probes) == 1:
                health['publication']['code_revision'] = 'previous-release'
                return json.dumps(health).encode()
        return data
    pauses = []
    assert verify_live(propagating_read, expected_commit=verified['code_revision'],
                       attempts=2, pause=pauses.append) == verified
    assert len(probes) == 2 and pauses == [10]
    # Tampered or mixed publications must fail verification before deployment.
    (root / 'api/update.json').write_text('{"revision":"wrong-version"}')
    with pytest.raises(AssertionError, match='revisions differ'):
        verify(read)
    with pytest.raises(AssertionError, match='revisions differ'):
        verify_live(read, attempts=2, pause=pauses.append)
