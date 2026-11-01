"""Indexable views of verified records and issued forecasts, without JavaScript."""
from collections import defaultdict
from datetime import date

from .links import event_path, fight_path, fighter_path
from .model import replay_with_methods
from .store import identity

ORIGIN = 'https://mma-oracle.web.app'


def percent(value):
    return f'{value:.1%}' if value is not None else 'Unavailable'


class Catalog:
    def __init__(self, service, dashboard=None):
        self.dashboard = dashboard or service.dashboard()
        self.verified = service.store.rows('fights')
        self.original = {f['id']: f for f in service.store.rows('predictions')}
        self.latest = {f['id']: f for f in service.store.rows('latest_predictions')}
        self.profiles = {identity(f['name']): dict(f) for f in self.dashboard['fighters']}
        self.events = {}
        self.bouts = defaultdict(list)
        self.forecasts = {}
        self.elo = {}
        replay_with_methods(self.verified, histories=self.elo)
        verified_ids = {f['id'] for f in self.verified}
        upcoming_ids = {f['id'] for f in self.dashboard['upcoming']}
        self.upcoming_ids = upcoming_ids
        for raw in self.verified + self.dashboard['upcoming']:
            forecast = self.original.get(raw['id']) if raw['id'] in verified_ids else self.latest.get(raw['id'])
            row = dict(forecast or {}, **raw)
            row.update(path=fight_path(row), event_path=event_path(row),
                       red_path=fighter_path(row['red']), blue_path=fighter_path(row['blue']))
            path = row['event_path']
            event = self.events.setdefault(path, dict(path=path, name=row.get('event') or 'UFC event', date=row['date'], fights=[]))
            event['fights'].append(row)
            if forecast or row['id'] in upcoming_ids:
                self.forecasts[row['path']] = row
            for corner in ('red', 'blue'):
                name = row[corner]
                self.profiles.setdefault(identity(name), dict(name=name))
                if row['id'] in verified_ids:
                    self.bouts[identity(name)].append(row)
        # Retain canceled/removed issued forecasts, clearly separated from cards.
        for forecast in self.latest.values():
            path = fight_path(forecast)
            if path not in self.forecasts:
                row = dict(forecast, path=path, event_path=event_path(forecast),
                                            red_path=fighter_path(forecast['red']), blue_path=fighter_path(forecast['blue']),
                                            removed=forecast['id'] not in upcoming_ids)
                self.forecasts[path] = row
                event = self.events.setdefault(row['event_path'], dict(path=row['event_path'],
                    name=row.get('event') or 'Archived UFC card', date=row['date'], fights=[]))
                event['fights'].append(row)
                for corner in ('red', 'blue'):
                    self.profiles.setdefault(identity(row[corner]), dict(name=row[corner]))
        self.fighters = {fighter_path(p['name']): p for p in self.profiles.values()}
        for event in self.events.values():
            for row in event['fights']:
                row.update(red_path=self.fighter_url(row['red']), blue_path=self.fighter_url(row['blue']))
        self.event_list = sorted(self.events.values(), key=lambda e: e['date'], reverse=True)

    def fighter_url(self, name):
        # UFCStats and bundled records may vary spaces/capitalization while
        # sharing the same normalized identity. All links use the profile name.
        return fighter_path(self.profiles.get(identity(name), {}).get('name', name))

    def common(self):
        if hasattr(self, '_common'):
            return dict(self._common)
        model = self.dashboard.get('model')
        self._common = dict(dashboard=self.dashboard, model=model, origin=ORIGIN,
                    fighter_path=self.fighter_url, event_path=event_path, fight_path=fight_path,
                    pct=percent, last_sync=self.dashboard['status'].get('last_sync'),
                    events=self.event_list, fighters=sorted(self.fighters.values(), key=lambda p: p['name']),
                    forecasts=sorted(self.forecasts.values(), key=lambda f: f['date'], reverse=True),
                    forecast_paths=set(self.forecasts),
                    upcoming_events=sorted((e for e in self.events.values() if any(f['id'] in self.upcoming_ids for f in e['fights'])), key=lambda e: e['date']))
        return dict(self._common)

    def fighter(self, path):
        profile = self.fighters.get(path)
        if profile is None:
            return None
        name = profile['name']
        rows = []
        wins = losses = other = 0
        points = self.elo.get(identity(name), [])
        rating_by_id = {p['id']: p for p in points}
        for fight in sorted(self.bouts[identity(name)], key=lambda f: (f['date'], f['id']), reverse=True):
            corner = 'red' if identity(fight['red']) == identity(name) else 'blue'
            opp = 'blue' if corner == 'red' else 'red'
            result = 'W' if fight.get('winner') == corner else 'L' if fight.get('winner') in ('red', 'blue') else 'D/NC'
            wins += result == 'W'
            losses += result == 'L'
            other += result == 'D/NC'
            rows.append(dict(fight, opponent=fight[opp], opponent_path=self.fighter_url(fight[opp]),
                             result=result, elo=rating_by_id.get(fight['id'], {}).get('after'),
                             detail_path=fight['path'] if fight['path'] in self.forecasts else fight['event_path']))
        upcoming = [f for f in self.dashboard['upcoming'] if identity(name) in (identity(f['red']), identity(f['blue']))]
        opponent = None
        if upcoming:
            next_fight = upcoming[0]
            opponent_name = next_fight['blue'] if identity(next_fight['red']) == identity(name) else next_fight['red']
            opponent = self.profiles.get(identity(opponent_name), dict(name=opponent_name))
        recent = [r for r in rows if r['result'] in ('W', 'L')][:5]
        return dict(profile=profile, history=rows, wins=wins, losses=losses, other=other,
                    upcoming=upcoming, opponent=opponent, recent=recent,
                    recent_wins=sum(r['result'] == 'W' for r in recent),
                    current_elo=points[-1]['after'] if points else None,
                    elo_points=points, elo_chart=self.chart(points),
                    coverage_start=min((r['date'] for r in rows), default=None))

    @staticmethod
    def chart(points):
        if not points:
            return None
        points = points[-40:]
        values = [p['after'] for p in points]
        low, high = min(values) - 20, max(values) + 20
        days = [date.fromisoformat(p['date']).toordinal() for p in points]
        span = max(1, max(days) - min(days))
        coords = [f'{52 + (day - min(days)) / span * 650:.1f},{170 - (value - low) / (high - low) * 140:.1f}'
                  for day, value in zip(days, values)]
        return dict(polyline=' '.join(coords), low=round(low), high=round(high),
                    start=points[0]['date'], end=points[-1]['date'])

    def page(self, path):
        data = self.common()
        data['path'] = path
        if path == '/fighters/':
            data.update(kind='fighters', title='UFC fighter records and profiles', description='Browse career snapshots, recorded UFC bouts, Elo histories, and upcoming fights.')
        elif path == '/events/':
            data.update(kind='events', title='UFC events and fight cards', description='Upcoming cards and verified historical UFC results, linked to fighter profiles and published forecasts.')
        elif path == '/fights/':
            data.update(kind='fights', title='Published UFC fight forecasts', description='Issued predictions, their model versions, supporting evidence, uncertainty, and settled results.')
        elif path == '/model/':
            data.update(kind='model', title='Model methods and measured performance', description='Serving estimators, actual input count, held-out metrics, probability reliability, and limitations.')
        elif path == '/status/':
            data.update(kind='status', title='Data and publication status', description='Last successful source pulls, published data counts, and production build provenance.')
        elif path in self.fighters:
            data.update(self.fighter(path))
            data.update(kind='fighter', title=data['profile']['name'] + ' — UFC record and Elo history',
                        description='Career snapshot, recorded UFC results, opponent history, striking and grappling statistics, and upcoming fights for ' + data['profile']['name'] + '.')
            data['structured'] = {'@context': 'https://schema.org', '@type': 'Person', 'name': data['profile']['name'], 'url': ORIGIN + path}
        elif path in self.events:
            event = self.events[path]
            data.update(kind='event', event=event, title=event['name'] + ' — ' + event['date'], description=f"Fight card, published forecasts, and available results for {event['name']} on {event['date']}.")
            data['structured'] = {'@context': 'https://schema.org', '@type': 'SportsEvent', 'name': event['name'], 'startDate': event['date'], 'url': ORIGIN + path, 'sport': 'Mixed martial arts'}
        elif path in self.forecasts:
            fight = self.forecasts[path]
            data.update(kind='fight', fight=fight, title=f"{fight['red']} vs. {fight['blue']} — {fight['date']}",
                        description=f"Issued MMA Cipher forecast for {fight['red']} vs. {fight['blue']}, with model evidence, data support, uncertainty, and available result.")
        else:
            return None
        return data

    def paths(self):
        return ['/fighters/', '/events/', '/fights/', '/model/', '/status/'] + sorted(set(self.fighters) | set(self.events) | set(self.forecasts))
