import logging
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import joblib

from .model import IMPLEMENTATION_HASH, SCHEMA, predict, train
from .model_metadata import describe_model
from .links import event_path, fight_path, fighter_path
from .odds import ESPNOdds, display_odds, forecast_open, match_market, odds_identity
from .source import UFCStats
from .store import identity, now
from .profiles import live_inputs

log = logging.getLogger(__name__)


class Service:
    def __init__(self, store, source=None, odds_source=None):
        self.store = store
        self.source = source or UFCStats()
        self.lock = threading.Lock()
        self.odds_lock = threading.Lock()
        self.profile_lock = threading.Lock()
        self.profile_attempts = {}
        self.auto_updates = False
        self.background_lock = threading.Lock()
        self.odds_source = odds_source or ESPNOdds()
        self.sync_interval = max(180, int(os.environ.get('SYNC_INTERVAL_SECONDS', '21600')))
        self.odds_interval = max(60, int(os.environ.get('ODDS_INTERVAL_SECONDS', '900')))
        self.odds_status = dict(running=False, error=None)
        self.bundle = None
        self._dashboard_lock = threading.Lock()
        self._dashboard_key = None
        self._dashboard_value = None
        self._dashboard_encoded = None
        self.dashboard_builds = 0
        self.model_path = store.directory / 'model.joblib'
        self.status = dict(running=False, phase='Ready', error=None)
        self.fighter_images = {}
        for p in [store.directory.parent / 'static' / 'fighter_images.json', Path('static/fighter_images.json')]:
            if p.exists():
                try:
                    import json
                    self.fighter_images = json.loads(p.read_text(encoding='utf-8'))
                    break
                except Exception:
                    log.exception('Could not read %s', p)
        if self.model_path.exists():
            try:
                self.bundle = joblib.load(self.model_path)
                if self.bundle.get('schema') != SCHEMA or self.bundle.get('implementation_hash') != IMPLEMENTATION_HASH:
                    self.bundle = None
            except Exception:
                log.exception('Cached model could not be read; rebuilding')

    def retrain(self):
        snapshots = self.store.meta('pre_fight_markets', {})
        fights = [dict(f, market_snapshot=snapshots[f['id']]) if f['id'] in snapshots else f for f in self.store.rows('fights')]
        bundle = train(fights)
        temp = self.model_path.with_suffix('.tmp')
        joblib.dump(bundle, temp)
        os.replace(temp, self.model_path)
        self.bundle = bundle
        self.store.set_meta('training_pending', False)

    def start_sync(self):
        if not self.lock.acquire(blocking=False):
            return False
        self.status = dict(running=True, phase='Fetching event schedule', error=None)
        try:
            self.store.set_meta('sync_attempt_at', now())
            threading.Thread(target=self._sync_worker, daemon=True).start()
        except Exception:
            self.status['running'] = False
            self.lock.release()
            raise
        return True

    def _sync_worker(self):
        try:
            if self.bundle is None:
                self.retrain()
            self.sync()
        except Exception as exc:
            log.exception('UFCStats refresh failed')
            self.status.update(error=str(exc), phase='Refresh unavailable')
            self.store.set_meta('sync_error', dict(message=str(exc), at=now()))
        finally:
            self.status['running'] = False
            self.lock.release()

    def sync(self):
        events = self.source.events()
        today = datetime.now(timezone.utc).date().isoformat()
        # Always revisit recent events for delayed results and official corrections.
        known = {f['id']: f for f in self.store.rows('fights')}
        cutoff = max((f['date'] for f in known.values()), default='1993-01-01')
        if self.store.meta('source_parser_version') != 2:
            # Reconcile earlier imports after parser upgrades (including NC/draw labels).
            cutoff = max((f['date'] for f in known.values() if f.get('source') == 'bundled ufc-master.csv'),
                         default='1993-01-01')
        revisit = (datetime.fromisoformat(cutoff)-timedelta(days=30)).date().isoformat()
        upcoming, changed, profile_urls = [], False, {}
        yesterday = (datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
        for event in events:
            if event['date'] < revisit:
                continue
            self.status['phase'] = f'Reading {event["name"]}'
            log.info('Reading %s (%s)', event['name'], event['date'])
            card = self.source.card(event)
            for fight in card:
                if fight['winner'] is not None and fight['date'] <= today:
                    previous = known.get(fight['id'])
                    if previous is None or any(previous.get(k) != fight.get(k) for k in ('winner', 'method', 'red', 'blue')):
                        self.store.set_meta('training_pending', True)
                        self.store.save_fight(fight)
                        known[fight['id']] = fight
                        changed = True
                elif fight['winner'] is None and fight['date'] >= yesterday:
                    upcoming.append(fight)
                # Refresh all fighters on upcoming and recently completed cards.
                if event['date'] >= (datetime.now(timezone.utc)-timedelta(days=14)).date().isoformat():
                    for corner in ('red', 'blue'):
                        url = fight[corner+'_url']
                        profile_urls[url] = fight[corner]
        if changed or self.store.meta('training_pending') or self.bundle is None:
            self.status['phase'] = 'Training on confirmed results'
            self.retrain()
        # Publish lineup changes promptly; a single profile outage must not leave
        # a removed opponent on the card or stop confirmed results from training.
        self.store.set_meta('upcoming', upcoming)
        self.refresh_predictions(upcoming)
        profiles_by_name = {identity(f['name']):f for f in self.store.rows('fighters')}
        watched = self.store.meta('watched_fighters', {})
        for name, requested_at in watched.items():
            if (datetime.now(timezone.utc)-datetime.fromisoformat(requested_at)).total_seconds() < 1800:
                profile = profiles_by_name.get(identity(name), {})
                if profile.get('url'):
                    profile_urls[profile['url']] = name
        errors = []
        for url, name in profile_urls.items():
            self.status['phase'] = 'Updating '+name
            try:
                self.store.save_fighter(self.source.fighter(url))
            except Exception as exc:
                log.warning('Profile update failed for %s: %s', name, exc)
                errors.append(name)
        self.refresh_predictions(upcoming)
        self.store.set_meta('profile_errors', errors)
        self.store.set_meta('last_sync', now())
        self.store.set_meta('source_parser_version', 2)
        self.store.set_meta('sync_error', None)
        self.status.update(phase='Up to date', error=None)

    def refresh_predictions(self, upcoming):
        if self.bundle is None:
            return
        markets = self.store.meta('markets', [])
        profiles = self.store.fighters_by_names(f[corner] for f in upcoming for corner in ('red','blue'))
        archive = self.store.meta('pre_fight_markets', {})
        archive_changed = False
        predictions = []
        for fight in upcoming:
            market = match_market(fight, markets)
            if not forecast_open(fight, market):
                continue
            if market and not market.get('error') and market.get('books') and market.get('fetched_at'):
                prices = market['books'][0]['prices']
                snapshot = dict(observed_at=market['fetched_at'], start_at=market.get('start_at'),
                                fight_date=fight['date'], source='ESPN',
                                prices={odds_identity(fight[c]): prices.get(odds_identity(fight[c])) for c in ('red','blue')})
                if archive.get(fight['id']) != snapshot:
                    archive[fight['id']] = snapshot
                    archive_changed = True
            observed = {corner: profiles.get(identity(fight[corner]), {}) for corner in ('red','blue')}
            bout_meta = live_inputs(fight, observed, start_at=market.get('start_at') if market else None)
            prediction = dict(
                fight,
                **predict(self.bundle, fight['red'], fight['blue'], fight['date'], bout_meta=bout_meta),
                issued_at=now(),
                input_provenance=bout_meta['input_provenance'],
                fighter_stats=observed,
                start_at=market.get('start_at') if market else None
            )
            if forecast_open(fight, market):
                predictions.append(prediction)
        self.store.publish_predictions(predictions)
        if archive_changed:
            self.store.set_meta('pre_fight_markets', archive)

    def sync_odds(self):
        previous = {m['id']:m for m in self.store.meta('markets', [])}
        schedule = self.odds_source.schedule()
        markets, failures = [], []
        for bout in schedule:
            if bout['state'] == 'post':
                markets.append(dict(bout, books=[], fetched_at=now()))
                continue
            try:
                books = self.odds_source.prices(bout)
                markets.append(dict(bout, books=books, fetched_at=now(), error=None))
            except Exception as exc:
                # Cached prices are marked stale, never stamped as newly fetched.
                old = previous.get(bout['id'], {})
                same_pair = old.get('athletes') == bout['athletes']
                markets.append(dict(bout, books=old.get('books', []) if same_pair else [],
                                    fetched_at=old.get('fetched_at') if same_pair else None,
                                    error='Sportsbook refresh unavailable'))
                failures.append(bout['id'])
                log.warning('Odds fetch failed for bout %s: %s', bout['id'], exc)
        metadata = dict(markets=markets, odds_feed_failed=False, odds_checked_at=now(),
                        odds_error=f'{len(failures)} markets could not be refreshed.' if failures else None)
        if not failures:
            metadata['odds_last_sync'] = now()
        self.store.set_meta_many(metadata)

    def start_odds_sync(self):
        if not self.odds_lock.acquire(blocking=False):
            return False
        self.odds_status = dict(running=True, error=None)
        try:
            threading.Thread(target=self._odds_worker, daemon=True).start()
        except Exception:
            self.odds_status['running'] = False
            self.odds_lock.release()
            raise
        return True

    def _odds_worker(self):
        try:
            self.sync_odds()
        except Exception as exc:
            log.exception('Odds refresh failed')
            self.odds_status['error'] = str(exc)
            self.store.set_meta('odds_feed_failed', True)
            self.store.set_meta('odds_error', 'ESPN odds feed is unavailable. Cached odds may be stale.')
        finally:
            self.odds_status['running'] = False
            self.odds_lock.release()

    def start_background_updates(self):
        with self.background_lock:
            if self.auto_updates:
                return False
            self.auto_updates = True
            threading.Thread(target=self.odds_scheduler, daemon=True).start()
            threading.Thread(target=self.scheduler, args=(self.sync_interval,), daemon=True).start()
            return True

    def odds_scheduler(self):
        while True:
            try:
                self.start_odds_sync()
            except Exception:
                log.exception('Could not start odds refresh; retrying next interval')
            threading.Event().wait(self.odds_interval)

    def refresh_selected_profiles(self, names):
        if not self.auto_updates or not self.profile_lock.acquire(blocking=False):
            return
        def worker():
            try:
                source = UFCStats()  # Separate HTTP/browser session from the card worker.
                profiles = self.store.fighters_by_names(names)
                for name in names:
                    attempted = self.profile_attempts.get(identity(name))
                    if attempted and (datetime.now(timezone.utc)-attempted).total_seconds() < self.sync_interval:
                        continue
                    old = profiles.get(identity(name), {})
                    fetched = old.get('fetched_at')
                    if fetched and (datetime.now(timezone.utc)-datetime.fromisoformat(fetched)).total_seconds() < self.sync_interval:
                        continue
                    url = old.get('url')
                    self.profile_attempts[identity(name)] = datetime.now(timezone.utc)
                    try:
                        self.store.save_fighter(source.fighter(url) if url else source.find_fighter(name))
                    except Exception as exc:
                        log.warning('Selected fighter refresh failed for %s: %s', name, exc)
            finally:
                self.profile_lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def get_dynamic_sync_interval(self):
        """Never leave schedule changes waiting for a weekly refresh."""
        return min(self.sync_interval, 21600)

    def sync_due(self, at=None):
        at = at or datetime.now(timezone.utc)
        if self.status['running']:
            return False
        attempted = self.store.meta('sync_attempt_at')
        if attempted and (at - datetime.fromisoformat(attempted)).total_seconds() < 300:
            return False
        completed = self.store.meta('last_sync')
        return (self.auto_updates and (self.bundle is None or self.store.meta('training_pending', False))) or not completed or (at - datetime.fromisoformat(completed)).total_seconds() >= self.get_dynamic_sync_interval()

    def scheduler(self, interval):
        while True:
            try:
                if self.sync_due():
                    self.start_sync()
            except Exception:
                log.exception('Could not start data refresh; retrying next interval')
            # Recheck after failures and event-day transitions without sleeping
            # through the entire data interval. start_sync prevents overlap.
            threading.Event().wait(60)

    def dashboard(self):
        """Return the shared computed snapshot; callers must not mutate it."""
        with self._dashboard_lock:
            key = (self.store.revision(), self.bundle.get('version') if self.bundle else None,
                   json.dumps(self.status, sort_keys=True), json.dumps(self.odds_status, sort_keys=True), int(time.time() // 60))
            if key != self._dashboard_key:
                self._dashboard_value = self._build_dashboard(self.store.snapshot())
                self._dashboard_encoded = json.dumps(self._dashboard_value, separators=(',', ':')).encode()
                self._dashboard_key = key
                self.dashboard_builds += 1
            return self._dashboard_value

    def dashboard_bytes(self):
        self.dashboard()
        return self._dashboard_encoded

    def _build_dashboard(self, snapshot):
        meta = lambda key, default=None: snapshot['meta'].get(key, default)
        today = datetime.now(timezone.utc).date().isoformat()
        fights = {f['id']:f for f in snapshot['tables']['fights']}
        saved = {p['id']:p for p in snapshot['tables']['predictions']}
        latest = {p['id']:p for p in snapshot['tables']['latest_predictions']}
        markets = meta('markets', [])
        odds_failed = meta('odds_feed_failed', False)
        upcoming = []
        for f in meta('upcoming', []):
            if f['date'] < (datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat() or f['id'] in fights:
                continue
            p = latest.get(f['id'], saved.get(f['id']))
            market = match_market(f, markets)
            if market and market.get('state') == 'post':
                continue
            if p:
                # Metadata comes from the newest card, prediction inputs from the
                # last eligible pre-event snapshot. No rewriting after the start.
                upcoming.append(dict(p, event=f.get('event'), weight_class=f.get('weight_class'),
                                     locked=not forecast_open(f, market), odds=display_odds(p, market, self.odds_interval, odds_failed)))
            else:
                upcoming.append(dict(f, prediction_pending=True, locked=not forecast_open(f, market),
                                     odds=display_odds(f, market, self.odds_interval, odds_failed)))
        results = []
        for p in saved.values():
            f = fights.get(p['id'])
            if f:
                winner = f.get(f['winner']) if f['winner'] in ('red', 'blue') else None
                results.append(dict(p, actual_winner=winner, correct=p['pick']==winner if winner else None,
                                    method=f.get('method')))
        scored = [r for r in results if r['correct'] is not None]
        for fight in upcoming + results:
            fight.update(path=fight_path(fight), event_path=event_path(fight),
                         red_path=fighter_path(fight['red']), blue_path=fighter_path(fight['blue']))
        bundle = self.bundle
        model_info = None
        if bundle:
            metrics = {key:value for key,value in bundle.get('metrics', {}).items() if key != 'holdout_bouts'}
            model_info = {
                'name': bundle.get('name', 'MMA Cipher Advanced Ensemble'),
                'version': bundle.get('version'),
                'schema': bundle.get('schema'),
                'trained_at': bundle.get('trained_at'),
                'through': bundle.get('through'),
                'metrics': metrics,
                'weights': bundle.get('weights'),
                'candidate_models': list(bundle.get('models', {}).keys()) or [bundle.get('name', 'Serving estimator')],
                'candidate_metrics': metrics.get('candidate_metrics'),
                'calibration': metrics.get('calibration'),
                'confidence_distribution': metrics.get('confidence_distribution'),
                'events_history': metrics.get('events_history'),
                'weight_class_stats': metrics.get('weight_class_stats'),
                'feature_importances': metrics.get('feature_importances')
            }
            model_info.update(describe_model(bundle))
            model_info['implementation_hash'] = bundle.get('implementation_hash')

        # Index recent verified bouts per fighter for the searchable database & profile views
        fights_by_fighter = defaultdict(list)
        for f in sorted(fights.values(), key=lambda x: x['date'], reverse=True):
            for corner, opp in (('red', 'blue'), ('blue', 'red')):
                won = f.get('winner') == corner
                res = 'W' if won else ('L' if f.get('winner') in ('red', 'blue') else 'NC/D')
                fights_by_fighter[identity(f[corner])].append({
                    'date': f['date'],
                    'event': f.get('event', ''),
                    'opponent': f[opp],
                    'result': res,
                    'method': f.get('method', '')
                })

        fighter_rows = []
        for f in sorted(snapshot['tables']['fighters'], key=lambda item: item['name']):
            f_copy = dict(f)
            f_copy['path'] = fighter_path(f['name'])
            bouts = fights_by_fighter.get(identity(f['name']), [])
            f_copy['recent_bouts'] = bouts[:6]
            f_copy['total_recorded_bouts'] = len(bouts)
            fighter_rows.append(f_copy)

        dynamic_interval = self.get_dynamic_sync_interval()
        profile_names = {identity(f['name']): f['name'] for f in fighter_rows}
        for fight in upcoming + results:
            for corner in ('red', 'blue'):
                fight[corner + '_path'] = fighter_path(profile_names.get(identity(fight[corner]), fight[corner]))
        return dict(revision=self.update_revision(snapshot), upcoming=sorted(upcoming, key=lambda f:f['date']), results=sorted(results, key=lambda f:f['date'], reverse=True),
                    fighters=fighter_rows,
                    images=self.fighter_images,
                    model=model_info,
                    status=dict(self.status, last_sync=meta('last_sync'), last_error=meta('sync_error')),
                    refresh=dict(stats_seconds=dynamic_interval, odds_seconds=self.odds_interval, browser_seconds=60,
                                 automatic=self.auto_updates),
                    odds_status=dict(self.odds_status, checked_at=meta('odds_checked_at'),
                                     last_sync=meta('odds_last_sync'), error=meta('odds_error')),
                    profile_errors=meta('profile_errors', []),
                    live_accuracy=sum(r['correct'] for r in scored)/len(scored) if scored else None,
                    scored_predictions=len(scored), total_fights=len(fights))

    def update_revision(self, snapshot=None):
        keys = ('last_sync','sync_error','odds_checked_at','odds_error','profile_errors')
        if snapshot is None:
            with self.store.scope() as db:
                if not db.in_transaction:
                    db.execute('BEGIN')
                data_revision = self.store.revision()
                metadata = self.store.meta_many(keys)
        else:
            data_revision = snapshot['revision']
            metadata = {key:snapshot['meta'][key] for key in keys if key in snapshot['meta']}
        state = dict(status=self.status, odds_status=self.odds_status,
                     model=self.bundle.get('version') if self.bundle else None,
                     data_revision=data_revision, meta=metadata)
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:24]

    def matchup(self, red, blue):
        fighters = self.store.fighters_by_names((red, blue))
        if identity(red) not in fighters or identity(blue) not in fighters:
            raise ValueError('Choose two fighters from the list.')
        if identity(red) == identity(blue):
            raise ValueError('Choose two different fighters.')
        red_p = fighters[identity(red)]
        blue_p = fighters[identity(blue)]
        red_name, blue_name = red_p['name'], blue_p['name']
        if self.auto_updates:
            self.refresh_selected_profiles((red_name, blue_name))

        at = datetime.now(timezone.utc)
        bout = dict(red=red_name, blue=blue_name, date=at.date().isoformat())
        bout_meta = live_inputs(bout, {'red':red_p, 'blue':blue_p}, at=at, start_at=at.isoformat())
        pred = predict(self.bundle, red_name, blue_name, bout['date'], bout_meta=bout_meta)
        pred['input_provenance'] = bout_meta['input_provenance']

        return dict(
            red=red_name,
            blue=blue_name,
            fighter_stats={'red': red_p, 'blue': blue_p},
            **pred
        )
