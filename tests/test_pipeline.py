from datetime import date, timedelta

import numpy as np
import pytest

from app import create_app
from predictor.model import FEATURES, features, predict, replay, train
from predictor.service import Service
from predictor.source import parse_card, parse_events, parse_fighter
from predictor.store import Store, bout_id, identity


def fight(day, red='Alice', blue='Beth', winner='red', method='KO/TKO'):
    return dict(id=bout_id(day, red, blue), date=day, red=red, blue=blue, winner=winner, method=method)


@pytest.fixture(scope='module')
def bundle():
    fights = [fight((date(2020,1,1)+timedelta(days=i*7)).isoformat(), winner='red' if i%3 else 'blue') for i in range(80)]
    return train(fights)


def test_no_future_or_same_day_outcome_leakage():
    first = fight('2020-01-01')
    same = fight('2020-01-01', 'Alice', 'Clara')
    later = fight('2020-02-01')
    rows, _ = replay([first, same, later])
    altered, _ = replay([dict(first,winner='blue'), same, dict(later,winner='blue')])
    for i in (0,1):
        np.testing.assert_array_equal(rows[i][1], np.zeros(len(FEATURES)))
        np.testing.assert_array_equal(rows[i][1], altered[i][1])
    assert not np.array_equal(rows[-1][1], altered[-1][1])
    # Changing the held-out bout's own result never changes its input.
    only_later, _ = replay([first, same, dict(later,winner='blue')])
    np.testing.assert_array_equal(rows[-1][1], only_later[-1][1])


def test_draws_and_no_contests_excluded():
    rows, states = replay([fight('2020-01-01', winner='other')])
    assert rows == [] and states == {}


def test_corner_swap_and_cold_start(bundle):
    a = predict(bundle, 'Alice', 'Beth', '2023-01-01')
    b = predict(bundle, 'Beth', 'Alice', '2023-01-01')
    assert a['red_probability'] == pytest.approx(b['blue_probability'])
    assert predict(bundle, 'New', 'Unknown', '2023-01-01')['red_probability'] == pytest.approx(.5)
    assert predict(bundle, 'New', 'Alice', '2023-01-01')['low_history']
    assert bundle['metrics']['test_fights'] == 16


def test_identity_and_rematches():
    assert identity('José Aldo') == identity('Jose Aldo')
    assert bout_id('2020-01-01','A','B') == bout_id('2020-01-01','B','A')
    assert bout_id('2020-01-01','A','B') != bout_id('2021-01-01','A','B')


def test_prediction_immutable_and_settlement(tmp_path, bundle):
    store = Store(tmp_path)
    service = Service(store); service.bundle = bundle
    p = dict(fight('2023-01-01'), **predict(bundle,'Alice','Beth','2023-01-01'), issued_at='2022-12-30T00:00:00+00:00')
    store.save_prediction(p)
    store.save_prediction(dict(p,pick='Changed',red_probability=.01))
    assert store.rows('predictions') == [p]
    store.save_fight(dict(fight('2023-01-01'),winner='other'))
    assert service.dashboard()['scored_predictions'] == 0
    store.save_fight(fight('2023-01-01', winner='blue'))
    assert service.dashboard()['scored_predictions'] == 1
    assert service.dashboard()['results'][0]['actual_winner'] == 'Beth'


class Source:
    def __init__(self):
        self.day = (date.today()+timedelta(days=7)).isoformat()
    def events(self):
        return [dict(name='Test event',date=self.day,url='http://ufcstats.com/event-details/1')]
    def card(self, event):
        return [dict(fight(self.day,winner=None),event='Test event',red_url='a',blue_url='b')]
    def fighter(self, url):
        return dict(name='Alice' if url=='a' else 'Beth', SLpM='4.5', fetched_at='2026-01-01')


def test_sync_idempotent_and_failure_retains_cache(tmp_path, bundle):
    store = Store(tmp_path); source=Source(); service=Service(store,source);service.bundle=bundle
    service.sync(); service.sync()
    assert len(store.rows('predictions')) == 1
    assert len(store.rows('fighters')) == 2
    before = store.meta('upcoming')
    source.events = lambda: (_ for _ in ()).throw(ValueError('Blocked page'))
    service.lock.acquire();service._sync_worker()
    assert store.meta('upcoming') == before
    assert store.meta('sync_error')['message'] == 'Blocked page'
    assert not service.status['running']


def test_confirmed_result_retrains_once_and_correction_retrains(tmp_path, bundle):
    store=Store(tmp_path); source=Source(); service=Service(store,source); service.bundle=bundle
    source.day='2024-01-01'
    outcome = fight(source.day)
    source.card=lambda e:[outcome]
    calls=[]
    def retrain():
        calls.append(True);store.set_meta('training_pending',False)
    service.retrain=retrain
    service.sync();service.sync()
    assert len(calls)==1 and len(store.rows('fights'))==1
    outcome['winner']='blue'
    service.sync()
    assert len(calls)==2


def test_same_day_forecast_not_issued(tmp_path,bundle):
    store=Store(tmp_path);source=Source();source.day=date.today().isoformat()
    service=Service(store,source);service.bundle=bundle;service.sync()
    assert store.rows('predictions')==[]


def test_newest_model_published_without_overwriting_original(tmp_path,bundle):
    store=Store(tmp_path);source=Source();service=Service(store,source);service.bundle=bundle
    service.sync()
    original=store.rows('predictions')[0]
    service.bundle=dict(bundle,version='new-model')
    service.sync()
    assert store.rows('latest_predictions')[0]['model_version']=='new-model'
    assert store.rows('predictions')[0]==original
    assert service.dashboard()['upcoming'][0]['model_version']=='new-model'


def test_same_day_with_known_start_updates_then_freezes(tmp_path,bundle):
    from datetime import datetime,timezone
    store=Store(tmp_path);source=Source();source.day=datetime.now(timezone.utc).date().isoformat()
    service=Service(store,source);service.bundle=bundle
    market=dict(id='m',event_id='event',date=source.day,athletes={'a':'Alice','b':'Beth'},state='pre',
                start_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),books=[])
    store.set_meta('markets',[market]);service.sync()
    assert len(store.rows('latest_predictions'))==1
    original=store.rows('latest_predictions')[0]
    store.set_meta('markets',[dict(market,start_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())])
    service.bundle=dict(bundle,version='after-start');service.sync()
    assert store.rows('latest_predictions')[0]==original
    assert service.dashboard()['upcoming'][0]['locked']


def test_card_changes_publish_even_when_profile_fails(tmp_path,bundle):
    store=Store(tmp_path);source=Source();service=Service(store,source);service.bundle=bundle;service.sync()
    original_id=store.rows('predictions')[0]['id']
    source.card=lambda e:[dict(fight(source.day,blue='Clara',winner=None),event='Changed card',red_url='a',blue_url='c')]
    source.fighter=lambda u:(_ for _ in ()).throw(ValueError('profile outage'))
    service.sync();upcoming=service.dashboard()['upcoming']
    assert len(upcoming)==1 and upcoming[0]['blue']=='Clara'
    assert upcoming[0]['id']!=original_id
    assert len(store.rows('predictions'))==2
    assert store.meta('profile_errors')==['Alice','Clara']


def test_source_parsers_and_challenge_detection():
    html='<tr class="b-statistics__table-row"><td><a href="http://ufcstats.com/event-details/abc">UFC Test</a><span class="b-statistics__date">September 19, 2026</span></td></tr>'
    events=parse_events(html)
    assert events[0]['date']=='2026-09-19'
    with pytest.raises(ValueError):parse_events('<p>Checking your browser</p>')
    card='<tr class="b-fight-details__table-row"><td>win</td><td><a href="http://ufcstats.com/fighter-details/a">Alice</a><a href="http://ufcstats.com/fighter-details/b">Beth</a></td><td>0</td><td>0</td><td>0</td><td>0</td><td>Flyweight</td><td>SUB</td></tr>'
    assert parse_card(card,events[0])[0]['winner']=='red'
    assert parse_card(card.replace('>win<','>nc nc<'),events[0])[0]['winner']=='other'
    assert parse_card(card.replace('>win<','>draw draw<'),events[0])[0]['winner']=='other'
    profile='<span class="b-content__title-highlight">Alice</span><span class="b-content__title-record">Record: 2-1-0</span><li class="b-list__box-list-item">SLpM: 4.5</li>'
    assert parse_fighter(profile,'http://ufcstats.com/fighter-details/a')['SLpM']=='4.5'


def test_api_input_validation_and_page(tmp_path,bundle):
    app=create_app(tmp_path,bootstrap=False);service=app.extensions['predictor'];service.bundle=bundle
    for n in ('Alice','Beth'):service.store.save_fighter(dict(name=n))
    client=app.test_client()
    assert client.get('/').status_code==200
    for body in ({},[],{'red':1,'blue':'Beth'},{'red':'Alice','blue':'Alice'},{'red':'x','blue':'Beth'}):
        assert client.post('/api/matchup',json=body).status_code==400
    assert client.post('/api/matchup',json={'red':'Alice','blue':'Beth'}).status_code==200
    assert client.get('/api/dashboard').json['model']['version']==bundle['version']


def test_evaluation_independent_of_winner_first_layout(bundle):
    fights=[]
    for i in range(80):
        f=fight((date(2020,1,1)+timedelta(days=i*7)).isoformat(),winner='red' if i%3 else 'blue')
        if f['winner']=='blue':
            f=dict(f,red=f['blue'],blue=f['red'],winner='red')
        fights.append(f)
    reordered=train(fights)
    assert reordered['metrics']==bundle['metrics']


def test_recent_features_only_use_earlier_outcomes():
    rows,states=replay([fight('2020-01-01'),fight('2020-02-01')])
    names=dict(zip(FEATURES,rows[1][1]))
    assert names['Recent overperformance'] == pytest.approx(1)
    assert names['Finish loss rate'] < 0
    assert names['Recent three form'] > 0
    assert len(states['alice']['residuals']) == 2


def test_holdout_outcomes_cannot_select_model(bundle):
    fights=[fight((date(2020,1,1)+timedelta(days=i*7)).isoformat(),winner='red' if i%3 else 'blue') for i in range(80)]
    for f in fights[64:]: f['winner']='blue' if f['winner']=='red' else 'red'
    changed=train(fights)
    assert changed['name']==bundle['name']
    assert changed['metrics']['validation_fold_log_loss']==bundle['metrics']['validation_fold_log_loss']
