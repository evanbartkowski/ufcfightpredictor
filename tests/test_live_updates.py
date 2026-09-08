from datetime import datetime, timedelta, timezone

import pytest

from predictor.odds import display_odds, forecast_open, match_market, parse_prices, parse_schedule
from predictor.service import Service
from predictor.store import Store, bout_id, identity


def market(day='2026-09-12'):
    return dict(id='111',event_id='222',date=day,start_at=day+'T18:00:00Z',state='pre',
                athletes={'1':'Jean Silva','2':'Jose Delgado'})


def test_moneylines_mapped_by_athlete_id_not_order_or_home():
    payload = {'items':[{'provider':{'name':'DraftKings'},
        'homeAthleteOdds':{'athlete':{'$ref':'https://sports.core.api.espn.com/v2/sports/mma/athletes/2?lang=en'},
                           'moneyLine':300, 'current':{'moneyLine':{'american':'+330'}}},
        'awayAthleteOdds':{'athlete':{'$ref':'https://sports.core.api.espn.com/v2/sports/mma/athletes/1'},
                           'moneyLine':-400, 'current':{'moneyLine':{'american':'-425'}}}}]}
    books = parse_prices(payload,market())
    assert books[0]['prices']=={'jeansilva':-425,'josedelgado':330}
    f=dict(red='José Delgado',blue='Jean Silva',date='2026-09-12')
    shown=display_odds(f,dict(market(),books=books,fetched_at=datetime.now(timezone.utc).isoformat()),120)
    assert shown['books'][0]['red']==330 and shown['books'][0]['blue']==-425
    payload['items'][0]['homeAthleteOdds']['current']['moneyLine']['american']='unavailable'
    assert parse_prices(payload,market())==[]


def test_ambiguous_rematch_and_changed_opponent_do_not_match():
    f=dict(red='Jean Silva',blue='Jose Delgado',date='2026-09-12')
    assert match_market(f,[market()]) is not None
    assert match_market(f,[market(),dict(market(),id='other')]) is None
    assert match_market(dict(f,blue='Someone Else'),[market()]) is None
    assert match_market(dict(f,date='2026-10-12'),[market()]) is None
    assert match_market(f,[dict(market(),athletes={'1':'Jean Silva','2':'Jose Miguel Delgado'})]) is not None


def test_event_start_freezes_forecasts_and_midnight_fallback():
    f=dict(red='Jean Silva',blue='Jose Delgado',date='2026-09-12')
    before=datetime(2026,9,12,17,59,tzinfo=timezone.utc)
    assert forecast_open(f,market(),before)
    assert not forecast_open(f,market(),before+timedelta(minutes=1))
    assert not forecast_open(f,dict(market(),state='in'),before)
    assert not forecast_open(f,None,before)


def test_schedule_uses_earliest_card_start_and_excludes_contender():
    c=dict(id='111',date='2026-09-12T22:00Z',timeValid=True,competitors=[
        {'id':'1','athlete':{'displayName':'Jean Silva'}},{'id':'2','athlete':{'displayName':'Jose Delgado'}}],
        status={'type':{'state':'pre'}})
    e=dict(id='222',name='Noche UFC',competitions=[c,dict(c,id='112',date='2026-09-12T18:00Z')])
    result=parse_schedule({'events':[e,dict(e,name="Dana White's Contender Series")]})
    assert len(result)==2 and all(b['start_at']=='2026-09-12T18:00Z' for b in result)
    with pytest.raises(ValueError):parse_schedule({'message':'upstream error'})


def test_prediction_versions_refresh_and_original_survives(tmp_path):
    s=Store(tmp_path)
    p=dict(id='fight',date='2026-09-12',red='A',blue='B',model_version='v1',red_probability=.55,
           features=[1],issued_at='2026-09-01T00:00:00Z',fighter_stats={'red':{'SLpM':3,'fetched_at':'2026-09-01'}})
    s.publish_prediction(p)
    s.publish_prediction(dict(p,issued_at='2026-09-02T00:00:00Z'))
    assert len(s.rows('prediction_revisions'))==1
    s.publish_prediction(dict(p,red_probability=.65,model_version='v2'))
    assert len(s.rows('prediction_revisions'))==2
    assert s.rows('predictions')[0]['red_probability']==.55
    assert s.rows('latest_predictions')[0]['red_probability']==.65


def test_odds_failure_keeps_old_timestamp_and_marks_stale(tmp_path):
    m=dict(market(),fetched_at=datetime.now(timezone.utc).isoformat(),
           books=[dict(name='DraftKings',prices={'jeansilva':-425,'josedelgado':330})])
    class Source:
        def schedule(self):return [market()]
        def prices(self,bout):raise ValueError('temporary failure')
    store=Store(tmp_path);store.set_meta('markets',[m]);service=Service(store,odds_source=Source())
    service.sync_odds();cached=store.meta('markets')[0]
    assert cached['fetched_at']==m['fetched_at']
    assert display_odds(dict(red='Jean Silva',blue='Jose Delgado'),cached,120)['stale']
    assert store.meta('odds_error')


def test_entire_feed_failure_marks_recent_cached_prices_stale():
    m=dict(market(),fetched_at=datetime.now(timezone.utc).isoformat(),books=[])
    assert display_odds(dict(red='Jean Silva',blue='Jose Delgado'),m,120,feed_failed=True)['stale']


def test_successful_empty_odds_clears_old_lines(tmp_path):
    class Source:
        def schedule(self):return [market()]
        def prices(self,bout):return []
    store=Store(tmp_path);service=Service(store,odds_source=Source());service.sync_odds()
    assert store.meta('markets')[0]['books']==[]
    assert store.meta('odds_error') is None


def test_background_start_is_idempotent(tmp_path, monkeypatch):
    targets=[]
    class Thread:
        def __init__(self, target, **kwargs): targets.append(target.__name__)
        def start(self): pass
    monkeypatch.setattr('predictor.service.threading.Thread', Thread)
    service=Service(Store(tmp_path))
    assert service.start_background_updates()
    assert not service.start_background_updates()
    assert targets == ['odds_scheduler', 'scheduler']
    assert service.dashboard()['refresh']['automatic']


def test_worker_start_failure_releases_lock(tmp_path, monkeypatch):
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): raise RuntimeError('thread unavailable')
    monkeypatch.setattr('predictor.service.threading.Thread', Thread)
    service=Service(Store(tmp_path))
    for start, lock, state in [(service.start_sync,service.lock,service.status),
                               (service.start_odds_sync,service.odds_lock,service.odds_status)]:
        with pytest.raises(RuntimeError): start()
        assert not lock.locked()
    assert not service.status['running']
    assert not service.odds_status['running']


def test_app_factory_enables_updates_and_respects_offline(tmp_path, monkeypatch):
    from app import create_app
    called=[]
    monkeypatch.setattr(Service, 'start_background_updates', lambda self: called.append(True))
    create_app(tmp_path, bootstrap=False, auto_updates=True)
    assert called == [True]
    create_app(tmp_path, bootstrap=False, auto_updates=False)
    assert called == [True]


def test_card_sections_require_repeated_broadcast_blocks():
    def bout(i, hour):
        return dict(id=str(i),date=f'2026-09-12T{hour}:00Z',timeValid=True,
                    competitors=[{'id':'1','athlete':{'displayName':'A'}},
                                 {'id':'2','athlete':{'displayName':'B'}}])
    def parse(bouts):
        return parse_schedule({'events':[dict(id='1',name='UFC test',competitions=bouts)]})
    blocks=[bout(i,hour) for i,hour in enumerate(['18','18','21','21'])]
    assert [b['card_section'] for b in parse(blocks)] == ['prelims','prelims','main','main']
    assert all(b['card_section'] is None for b in parse([bout(1,'18'),bout(2,'21')]))
    assert all(b['card_section'] is None for b in parse([bout(1,'18'),bout(2,'18')]))
    early=[bout(i,hour) for i,hour in enumerate(['16','16','18','18','21','21'])]
    assert [b['card_section'] for b in parse(early)] == ['early','early','prelims','prelims','main','main']
