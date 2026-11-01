import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from predictor.publication import export_state, restore_state, update_publication
from predictor.service import Service
from predictor.store import Store


def test_public_state_retains_original_forecasts_and_excludes_local_activity(tmp_path):
    store = Store(tmp_path / 'original')
    p = dict(id='bout', date='2027-01-01', red='A', blue='B', pick='A', red_probability=.6)
    store.publish_prediction(p)
    store.publish_prediction(dict(p, red_probability=.7))
    store.save_fighter(dict(name='A', SLpM='3.5'))
    store.set_meta('last_sync', '2026-11-01T12:00:00+00:00')
    store.set_meta('watched_fighters', {'A': 'private browsing activity'})
    state = export_state(store)
    assert b'watched_fighters' not in gzip.decompress(state)
    restored = Store(tmp_path / 'restored')
    restore_state(restored, state)
    assert restored.rows('predictions')[0]['red_probability'] == .6
    assert restored.rows('latest_predictions')[0]['red_probability'] == .7
    assert len(restored.rows('prediction_revisions')) == 2
    assert restored.rows('fighters')[0]['SLpM'] == '3.5'
    assert restored.meta('last_sync') == store.meta('last_sync')


def test_bad_state_cannot_partially_erase_existing_data(tmp_path):
    store = Store(tmp_path)
    store.save_fight(dict(id='existing', date='2026-01-01'))
    state = json.loads(gzip.decompress(export_state(store)))
    state['tables']['fighters'] = [{}]
    with pytest.raises(KeyError):
        restore_state(store, gzip.compress(json.dumps(state).encode()))
    assert store.rows('fights') == [dict(id='existing', date='2026-01-01')]


def test_due_checks_poll_soon_after_failure_instead_of_waiting_a_week(tmp_path):
    store = Store(tmp_path)
    service = Service(store)
    at = datetime.now(timezone.utc)
    assert service.sync_due(at)
    store.set_meta('last_sync', (at - timedelta(days=1)).isoformat())
    store.set_meta('sync_attempt_at', (at - timedelta(minutes=1)).isoformat())
    assert not service.sync_due(at)
    assert service.sync_due(at + timedelta(minutes=5))
    store.set_meta('last_sync', at.isoformat())
    assert not service.sync_due(at + timedelta(hours=5))
    assert service.sync_due(at + timedelta(hours=6))
    assert service.dashboard()['refresh']['automatic'] is False


def test_failed_upstream_is_published_with_old_timestamp_and_retry_status(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    old = '2026-10-11T12:00:00+00:00'
    store.set_meta('last_sync', old)
    service = Service(store)
    def fail():
        raise ValueError('upstream unavailable')
    monkeypatch.setattr(service, 'sync', fail)
    monkeypatch.setattr(service, 'sync_odds', fail)
    output = tmp_path / 'public'
    assert len(update_publication(service, output)) == 2
    dashboard = json.loads((output / 'dashboard.json').read_text())
    assert dashboard['status']['last_sync'] == old
    assert dashboard['status']['last_error']['message'] == 'upstream unavailable'
    assert dashboard['refresh']['delivery'] == 'scheduled'
    assert dashboard['refresh']['browser_seconds'] == 60
    restored = Store(tmp_path / 'restored')
    restore_state(restored, (output / 'sync-state.json.gz').read_bytes())
    assert restored.meta('last_sync') == old


def test_recent_success_skips_stats_but_updates_odds(tmp_path, monkeypatch):
    service = Service(Store(tmp_path / 'data'))
    service.store.set_meta('last_sync', datetime.now(timezone.utc).isoformat())
    calls = []
    monkeypatch.setattr(service, 'sync', lambda: calls.append('stats'))
    monkeypatch.setattr(service, 'sync_odds', lambda: calls.append('odds'))
    assert update_publication(service, tmp_path / 'public') == []
    assert calls == ['odds']


def test_snapshot_cleanup_preserves_live_manual_and_recent_releases():
    from scripts.hosting_retention import retired_versions
    def release(n, message='[mma-cipher-auto] update'):
        return dict(message=message, releaseTime=f'2026-11-01T12:{n:02}:00Z',
                    version=dict(name=f'version-{n}', status='FINALIZED'))
    releases = [release(n) for n in range(30)]
    releases += [release(1, 'Manual rollback'), release(31, 'Manual release')]
    assert retired_versions(releases, active='version-0') == ['version-2', 'version-3', 'version-4', 'version-5']
