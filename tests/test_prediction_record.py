from predictor.service import Service
from predictor.store import Store, bout_id
from predictor.publication import export_state, restore_state


def save_pick(store, label, winner='red', section=None, verified_section=None):
    fight = dict(id=bout_id('2026-10-17',label,'Opponent'),date='2026-10-17',
                 red=label,blue='Opponent',event='Test UFC',winner=winner,method='Decision')
    pick = dict(fight,pick=label,confidence=.6,issued_at='2026-10-16T12:00:00Z')
    if section: pick['card_section'] = section
    if verified_section: fight['card_section'] = verified_section
    store.save_prediction(pick); store.save_fight(fight)
    return pick


def test_record_counts_only_main_card_and_leaves_every_original_pick_intact(tmp_path):
    store = Store(tmp_path); service = Service(store)
    win = save_pick(store,'Main win',section='main')
    loss = save_pick(store,'Main loss',winner='blue')
    draw = save_pick(store,'Main draw',winner='other',verified_section='main')
    save_pick(store,'Prelim win',section='prelims')
    save_pick(store,'Early loss',winner='blue',section='early')
    save_pick(store,'Unknown win')
    moved = save_pick(store,'Moved to prelims',section='main')
    store.set_meta('card_sections',{
        loss['id']:dict(card_section='main',final=True,section_source='scheduled broadcast blocks'),
        moved['id']:dict(card_section='prelims',final=True)})
    originals = store.rows('predictions')
    result = service.dashboard()
    assert result['record_scope'] == 'main_card'
    assert {r['id'] for r in result['results']} == {win['id'],loss['id'],draw['id']}
    assert all(r['card_section']=='main' for r in result['results'])
    assert result['scored_predictions'] == 2 and result['live_accuracy'] == .5
    assert next(r for r in result['results'] if r['id']==draw['id'])['correct'] is None
    assert store.rows('predictions') == originals


def test_historical_placement_matches_reversed_corners_and_survives_a_fresh_runner(tmp_path):
    store = Store(tmp_path/'first'); pick = save_pick(store,'Main fighter')
    calls = []
    class Source:
        def historical_schedule(self, day):
            calls.append(day)
            return [dict(id='espn-bout',event_id='espn-event',date='2026-10-18',state='post',
                         athletes={'1':'Opponent','2':'Main fighter'},card_section='main',
                         section_source='scheduled broadcast blocks')]
    service = Service(store,odds_source=Source())
    assert service.dashboard()['scored_predictions'] == 0
    service.sync_record_sections()
    assert calls == ['2026-10-17']
    assert service.dashboard()['scored_predictions'] == 1
    assert store.rows('predictions') == [pick]
    restored = Store(tmp_path/'restored'); restore_state(restored,export_state(store))
    worker = Service(restored,odds_source=Source()); worker.sync_record_sections()
    assert calls == ['2026-10-17']
    assert worker.dashboard()['scored_predictions'] == 1


def test_unavailable_historical_placement_does_not_guess_a_main_card(tmp_path):
    store = Store(tmp_path); save_pick(store,'Unclassified fighter')
    class Source:
        def historical_schedule(self, day):
            raise ValueError('Feed unavailable')
    service = Service(store,odds_source=Source()); service.sync_record_sections()
    assert service.dashboard()['results'] == []
    assert service.dashboard()['scored_predictions'] == 0
    assert service.dashboard()['live_accuracy'] is None
