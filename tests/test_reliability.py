from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import date,datetime,timedelta,timezone
import random

import numpy as np
import pytest
import requests

from app import create_app
from predictor.market_inputs import verified_market,market_features
from predictor.model import FEATURES,features,fresh,replay,train,compare_market_models
from predictor.profiles import normalize_profile,live_inputs
from predictor.service import Service
from predictor.source import UFCStats,parse_card
from predictor.store import Store,bout_id,identity
from scripts.monitor_production import assess
from test_public_pages import public_app


def bout(day,red='Alice',blue='Beth',winner='red',**extra):
    return dict(id=bout_id(day,red,blue),date=day,red=red,blue=blue,winner=winner,method='KO/TKO',**extra)


def snapshot(fight):
    return dict(fight_date=fight['date'],observed_at=fight['date']+'T17:00:00Z',start_at=fight['date']+'T18:00:00Z',
                source='ESPN',prices={identity(fight['red']):-200,identity(fight['blue']):150})


def test_profile_units_percentages_birthdate_and_missing_values():
    values=normalize_profile(dict(height='5\' 11"',reach='74"',weight='77 kg',dob='Feb 29, 1992',
                                  sig_str_acc='45%',td_acc='0%',td_def='65',SLpM='4.2',td_avg='--',sub_avg='nan'))
    assert values['height']==pytest.approx(180.34)
    assert values['reach']==pytest.approx(187.96)
    assert values['weight']==pytest.approx(169.756,abs=.01)
    assert values['sig_str_pct']==.45 and values['td_pct']==0 and values['td_def']==.65
    assert values['dob']=='1992-02-29'
    assert 'td_landed' not in values and 'sub_att' not in values


def test_eligible_refresh_changes_the_correct_live_inputs_and_rejects_future_profiles():
    fight=bout('2026-11-07',winner=None)
    at=datetime(2026,11,1,12,tzinfo=timezone.utc)
    profile=dict(fetched_at='2026-11-01T10:00:00Z',height='6\' 0"',reach='74"',dob='Nov 08, 1990',sig_str_acc='45%',SLpM='4.5')
    meta=live_inputs(fight,{'red':profile},at=at)
    x=features({},'Alice','Beth',fight['date'],meta)
    assert x[16]==5 # Age 35, not the baseline age 30.
    assert x[17]==pytest.approx(182.88-175)
    assert x[18]==pytest.approx(187.96-175)
    assert x[20]==pytest.approx(1.0)
    assert x[21]==pytest.approx(0.)
    updated=live_inputs(fight,{'red':dict(profile,sig_str_acc='60%')},at=at)
    changed=features({},'Alice','Beth',fight['date'],updated)
    assert changed[21]-x[21]==pytest.approx(.15)
    rejected=live_inputs(fight,{'red':dict(profile,fetched_at='2026-11-08T12:00:00Z')},at=at)
    np.testing.assert_array_equal(features({},'Alice','Beth',fight['date'],rejected),features({},'Alice','Beth',fight['date']))
    assert not rejected['input_provenance']['profiles']['red']['eligible']


def test_market_free_policy_and_strict_timestamped_odds():
    fight=bout('2026-11-07',winner=None)
    enriched=dict(fight,red_odds=-1000,blue_odds=800,market_snapshot=snapshot(fight))
    np.testing.assert_array_equal(features({},'Alice','Beth',fight['date'],fight),features({},'Alice','Beth',fight['date'],enriched))
    assert len(FEATURES)==37 and verified_market(enriched)==(-200,150)
    assert market_features(enriched)[0]>0
    for altered in [dict(snapshot(fight),observed_at='2026-11-07T19:00:00Z'),
                    dict(snapshot(fight),prices={'alice':-200,'replacement':150}),
                    dict(snapshot(fight),fight_date='2026-11-08'),
                    dict(snapshot(fight),observed_at='2026-10-01T12:00:00Z')]:
        assert verified_market(dict(fight,market_snapshot=altered)) is None
    assert compare_market_models([enriched])['status']=='insufficient_data'


def test_market_comparison_uses_identical_eligible_bouts_and_dates():
    fights=[]
    for index in range(120):
        day=(date(2020,1,1)+timedelta(days=(index//6)*7)).isoformat()
        f=bout(day,red=f'A{index%6}',blue=f'B{index%6}',winner='red' if index%2 else 'blue')
        f['market_snapshot']=snapshot(f);fights.append(f)
    result=compare_market_models(fights)
    assert result['status']=='measured' and result['eligible_bouts']==120 and result['test_bouts']==24
    assert set(result['scores'])=={'market_free','odds_aware'}
    assert result['test_start']=='2020-04-22'


def test_unsorted_draws_and_same_day_events_preserve_holdout_attribution():
    fights=[]
    for index in range(40):
        day=(date(2020,1,1)+timedelta(days=index*7)).isoformat()
        for n in range(2):
            fights.append(bout(day,red=f'A{n}',blue=f'B{n}',winner='red' if index%3 else 'blue',
                               event=f'Event {n} on {day}',weight_class='Flyweight' if n==0 else 'Heavyweight'))
        fights.append(bout(day,red='Draw A',blue='Draw B',winner='other',event='Draw only',weight_class='Draw division'))
    random.Random(31).shuffle(fights)
    result=train(fights)['metrics']
    source={f['id']:f for f in fights}
    assert result['test_fights']==16 and len(result['holdout_bouts'])==16
    assert len(result['events_history'])==16 # Two separately attributed events per day.
    for row in result['holdout_bouts']:
        assert source[row['id']]['winner'] in ('red','blue')
        assert row['event']==source[row['id']]['event']
        assert row['weight_class']==source[row['id']]['weight_class']
        assert row['date']==source[row['id']]['date']
    for row in result['calibration']:
        if row['count']==0:assert row['observed'] is None and row['expected'] is None


def test_dashboard_cache_invalidates_after_commit_but_not_rollback(public_app):
    service=public_app.extensions['predictor'];store=service.store
    first=service.dashboard_bytes();builds=service.dashboard_builds
    for _ in range(20):assert service.dashboard_bytes() is first
    assert service.dashboard_builds==builds
    store.save_fighter(dict(name='New profile'))
    assert service.dashboard_bytes()!=first and service.dashboard_builds==builds+1
    committed=service.dashboard_bytes()
    with pytest.raises(RuntimeError):
        with store.scope():
            store.save_fighter(dict(name='Rolled back'))
            raise RuntimeError('rollback')
    assert service.dashboard_bytes() is committed


def test_wal_atomic_batches_and_scoped_connections_under_concurrency(tmp_path):
    store=Store(tmp_path)
    def writer():
        for index in range(30):store.set_meta_many(dict(left=index,right=index))
    def reader():
        for _ in range(30):
            meta=store.snapshot()['meta']
            assert meta.get('left')==meta.get('right')
    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks=[pool.submit(writer)]+[pool.submit(reader) for _ in range(4)]
        for task in tasks:task.result()
    with store.connect() as db:assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    before=store.diagnostics()['connections']
    with store.scope():
        for _ in range(10):store.meta('left')
    assert store.diagnostics()['connections']==before+1


def test_indexed_apis_and_matchups_do_not_read_whole_tables(public_app,monkeypatch):
    service=public_app.extensions['predictor']
    monkeypatch.setattr(service.store,'rows',lambda *args:pytest.fail('Unexpected full table read'))
    client=public_app.test_client()
    fighters=client.get('/api/fighters?q=Alice&per_page=1').json
    assert fighters['total']==1 and len(fighters['items'])==1
    assert client.get('/api/fighters/Alice/history?per_page=1').json['total']==2
    assert client.post('/api/matchup',json=dict(red='Alice',blue='Beth')).status_code==200
    assert client.get('/api/fighters?per_page=500').status_code==400
    assert client.get('/api/fighters?page=-1').status_code==400


def test_rate_limit_validation_and_readiness(public_app):
    client=public_app.test_client();limiter=public_app.extensions['matchup_limiter'];limiter.per_ip=2
    assert client.post('/api/matchup',json=dict(red='Alice',blue='Beth')).status_code==200
    assert client.post('/api/matchup',json=dict(red='x'*101,blue='Beth')).status_code==400
    response=client.post('/api/matchup',json=dict(red='Alice',blue='Beth'))
    assert response.status_code==429 and response.headers['Retry-After']=='60'
    assert client.get('/ready').status_code==200
    public_app.extensions['predictor'].bundle=None
    assert client.get('/health').status_code==200
    assert client.get('/ready').status_code==503


def test_factory_does_not_train_synchronously(tmp_path,monkeypatch):
    store=Store(tmp_path);store.set_meta_many(dict(seeded=True,seeded_schema=5))
    monkeypatch.setattr(Service,'retrain',lambda self:pytest.fail('Training ran in web factory'))
    app=create_app(tmp_path,bootstrap=True,auto_updates=False)
    assert app.test_client().get('/ready').status_code==503


def test_scraper_prefers_https_allows_refused_port_fallback_and_blocks_redirects(monkeypatch):
    import predictor.source as source
    monkeypatch.setattr(source,'_http_only_until',0)
    monkeypatch.setattr(source.UPSTREAM,'request',lambda:nullcontext())
    calls=[];http=UFCStats()
    def get(url,**kwargs):
        calls.append(url)
        if url.startswith('https:'):raise requests.ConnectionError('port refused')
        result=requests.Response();result.status_code=200;result._content=b'official stats';return result
    monkeypatch.setattr(http.session,'get',get)
    assert http.get('http://ufcstats.com/statistics/events/completed')=='official stats'
    assert calls[0].startswith('https:') and calls[1].startswith('http:')
    for bad in ('http://127.0.0.1/','http://ufcstats.com:1234/','http://user@ufcstats.com/'):
        with pytest.raises(ValueError):http.get(bad)
    def redirect(*args,**kwargs):
        result=requests.Response();result.status_code=302;result.headers['Location']='http://127.0.0.1/';return result
    monkeypatch.setattr(http.session,'get',redirect)
    with pytest.raises(ValueError):http.get('http://ufcstats.com/event-details/1')


def test_scraper_certificate_errors_and_429_are_not_downgraded_or_retried(monkeypatch):
    import predictor.source as source
    monkeypatch.setattr(source,'_http_only_until',0);monkeypatch.setattr(source.UPSTREAM,'request',lambda:nullcontext())
    http=UFCStats();calls=[]
    def invalid(*args,**kwargs):calls.append(1);raise requests.exceptions.SSLError('invalid certificate')
    monkeypatch.setattr(http.session,'get',invalid)
    with pytest.raises(requests.exceptions.SSLError):http.get('https://ufcstats.com/event-details/1')
    assert len(calls)==1
    def limited(*args,**kwargs):
        result=requests.Response();result.status_code=429;result._content=b'rate limited';return result
    monkeypatch.setattr(http.session,'get',limited)
    with pytest.raises(requests.HTTPError):http.get('https://ufcstats.com/event-details/1')


def test_monitor_detects_silent_schedule_failure():
    at=datetime(2026,11,1,12,tzinfo=timezone.utc)
    health=dict(published_at=at.isoformat(),last_sync=at.isoformat(),odds_checked_at=at.isoformat(),model_version='v')
    assert assess(health,at)==[]
    assert 'heartbeat' in assess(health,at+timedelta(hours=1))[0]
