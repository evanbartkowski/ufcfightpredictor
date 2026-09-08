import logging
import os
import threading
from datetime import datetime, timezone, timedelta

import joblib

from .model import SCHEMA, predict, train
from .odds import ESPNOdds, display_odds, forecast_open, match_market
from .source import UFCStats
from .store import identity, now

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
        self.sync_interval = max(180, int(os.environ.get('SYNC_INTERVAL_SECONDS', '3600')))
        self.odds_interval = max(60, int(os.environ.get('ODDS_INTERVAL_SECONDS', '900')))
        self.odds_status = dict(running=False, error=None)
        self.bundle = None
        self.model_path = store.directory / 'model.joblib'
        self.status = dict(running=False, phase='Ready', error=None)
        if self.model_path.exists():
            try:
                self.bundle = joblib.load(self.model_path)
                if self.bundle.get('schema') != SCHEMA:
                    self.bundle = None
            except Exception:
                log.exception('Cached model could not be read; rebuilding')

    def retrain(self):
        bundle = train(self.store.rows('fights'))
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
            threading.Thread(target=self._sync_worker, daemon=True).start()
        except Exception:
            self.status['running'] = False
            self.lock.release()
            raise
        return True

    def _sync_worker(self):
        try:
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
        markets = self.store.meta('markets', [])
        profiles = {identity(f['name']):f for f in self.store.rows('fighters')}
        for fight in upcoming:
            market = match_market(fight, markets)
            if not forecast_open(fight, market):
                continue
            prediction = dict(fight, **predict(self.bundle, fight['red'], fight['blue'], fight['date']),
                              issued_at=now(), fighter_stats={corner:profiles.get(identity(fight[corner]), {})
                                                             for corner in ('red', 'blue')},
                              start_at=market.get('start_at') if market else None)
            self.store.publish_prediction(prediction)

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
        self.store.set_meta('markets', markets)
        self.store.set_meta('odds_feed_failed', False)
        self.store.set_meta('odds_checked_at', now())
        self.store.set_meta('odds_error', f'{len(failures)} markets could not be refreshed.' if failures else None)
        if not failures:
            self.store.set_meta('odds_last_sync', now())

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
                profiles = {identity(p['name']):p for p in self.store.rows('fighters')}
                urls = {identity(f[corner]):f.get(corner+'_url') for f in self.store.rows('fights')
                        for corner in ('red', 'blue') if f.get(corner+'_url')}
                for name in names:
                    attempted = self.profile_attempts.get(identity(name))
                    if attempted and (datetime.now(timezone.utc)-attempted).total_seconds() < self.sync_interval:
                        continue
                    old = profiles.get(identity(name), {})
                    fetched = old.get('fetched_at')
                    if fetched and (datetime.now(timezone.utc)-datetime.fromisoformat(fetched)).total_seconds() < self.sync_interval:
                        continue
                    url = old.get('url') or urls.get(identity(name))
                    self.profile_attempts[identity(name)] = datetime.now(timezone.utc)
                    try:
                        self.store.save_fighter(source.fighter(url) if url else source.find_fighter(name))
                    except Exception as exc:
                        log.warning('Selected fighter refresh failed for %s: %s', name, exc)
            finally:
                self.profile_lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def scheduler(self, interval):
        while True:
            try:
                self.start_sync()
            except Exception:
                log.exception('Could not start data refresh; retrying next interval')
            threading.Event().wait(interval)

    def dashboard(self):
        today = datetime.now(timezone.utc).date().isoformat()
        fights = {f['id']:f for f in self.store.rows('fights')}
        saved = {p['id']:p for p in self.store.rows('predictions')}
        latest = {p['id']:p for p in self.store.rows('latest_predictions')}
        markets = self.store.meta('markets', [])
        odds_failed = self.store.meta('odds_feed_failed', False)
        upcoming = []
        for f in self.store.meta('upcoming', []):
            if f['date'] < (datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat() or f['id'] in fights:
                continue
            p = latest.get(f['id'], saved.get(f['id']))
            market = match_market(f, markets)
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
        bundle = self.bundle
        return dict(upcoming=sorted(upcoming, key=lambda f:f['date']), results=sorted(results, key=lambda f:f['date'], reverse=True),
                    fighters=sorted(self.store.rows('fighters'), key=lambda f:f['name']),
                    model={k:v for k,v in bundle.items() if k not in ('model','states')} if bundle else None,
                    status=dict(self.status, last_sync=self.store.meta('last_sync'), last_error=self.store.meta('sync_error')),
                    refresh=dict(stats_seconds=self.sync_interval, odds_seconds=self.odds_interval, browser_seconds=60,
                                 automatic=self.auto_updates),
                    odds_status=dict(self.odds_status, checked_at=self.store.meta('odds_checked_at'),
                                     last_sync=self.store.meta('odds_last_sync'), error=self.store.meta('odds_error')),
                    profile_errors=self.store.meta('profile_errors', []),
                    live_accuracy=sum(r['correct'] for r in scored)/len(scored) if scored else None,
                    scored_predictions=len(scored), total_fights=len(fights))

    def matchup(self, red, blue):
        names = {identity(f['name']):f['name'] for f in self.store.rows('fighters')}
        if identity(red) not in names or identity(blue) not in names:
            raise ValueError('Choose two fighters from the list.')
        if identity(red) == identity(blue):
            raise ValueError('Choose two different fighters.')
        red, blue = names[identity(red)], names[identity(blue)]
        if self.auto_updates:
            watched = self.store.meta('watched_fighters', {})
            for name in (red, blue):
                watched[name] = now()
            self.store.set_meta('watched_fighters', dict(sorted(watched.items(),key=lambda item:item[1])[-20:]))
            self.refresh_selected_profiles((red,blue))
        return dict(red=red, blue=blue, **predict(self.bundle, red, blue, datetime.now(timezone.utc).date().isoformat()))
