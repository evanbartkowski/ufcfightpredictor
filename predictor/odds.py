"""Public ESPN UFC schedule and bookmaker moneylines; never infer corner order."""
import math
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .store import identity, now

SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard'
CORE = 'https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc'

# Explicit cross-provider names verified on the same dated UFC cards. No fuzzy
# surname matching: an uncertain match must never attach somebody else's price.
NAME_ALIASES = {'josemigueldelgado':'josedelgado', 'seankingiii':'seanking',
                'michaelaswelljr':'michaelaswell'}


def odds_identity(name):
    key = identity(name)
    return NAME_ALIASES.get(key, key)


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Source time has no timezone')
    return result


def parse_schedule(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
        raise ValueError('Unrecognized ESPN event feed')
    bouts = []
    for event in payload['events']:
        if 'ufc' not in event.get('name', '').lower():
            continue  # The UFC league feed also includes Contender Series.
        competitions = event.get('competitions', [])
        starts = [c.get('date') for c in competitions if c.get('timeValid') and c.get('date')]
        start = min(starts, key=timestamp) if starts else None
        # Repeated broadcast-time blocks distinguish card sections; individual
        # bout timestamps or a single unconfirmed block cannot establish a split.
        blocks = sorted(set(starts), key=timestamp)
        grouped = 2 <= len(blocks) <= 3 and all(starts.count(t) >= 2 for t in blocks)
        for c in competitions:
            competitors = c.get('competitors', [])
            if len(competitors) != 2:
                continue
            athletes = {str(a['id']): a['athlete']['displayName'] for a in competitors}
            if any(identity(n) in ('tba', 'opponenttba') for n in athletes.values()):
                continue
            if len(athletes) != 2 or len({identity(n) for n in athletes.values()}) != 2:
                continue
            bouts.append(dict(id=str(c['id']), event_id=str(event['id']), event=event['name'],
                              date=c['date'][:10], start_at=start,
                              state=c.get('status', {}).get('type', {}).get('state', 'unknown'),
                              athletes=athletes, schedule_fetched_at=now(),
                              card_section=('main' if c.get('date') == blocks[-1] else
                                            'early' if len(blocks) == 3 and c.get('date') == blocks[0] else
                                            'prelims') if grouped and c.get('timeValid') and c.get('date') in blocks else None,
                              section_source='scheduled broadcast blocks' if grouped else None))
    return bouts


def moneyline(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and abs(n) >= 100 and n.is_integer() else None
    except (TypeError, ValueError):
        return None


def parse_prices(payload, bout):
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise ValueError('Unrecognized ESPN odds feed')
    books = []
    for item in payload['items']:
        prices = {}
        for field in ('homeAthleteOdds', 'awayAthleteOdds'):
            side = item.get(field, {})
            ref = side.get('athlete', {}).get('$ref', '')
            athlete_id = urlparse(ref).path.rstrip('/').rsplit('/', 1)[-1]
            name = bout['athletes'].get(athlete_id)
            current = side.get('current', {}).get('moneyLine', {})
            # Never substitute the opening line for a missing current line.
            value = current.get('american', side.get('moneyLine'))
            price = moneyline(value)
            if name and price is not None:
                prices[odds_identity(name)] = price
        if len(prices) == 2:
            provider = item.get('provider', {})
            books.append(dict(name=provider.get('name', 'Unknown sportsbook'),
                              priority=provider.get('priority', 999), prices=prices,
                              updated_at=item.get('lastUpdated') or item.get('lastModified')))
    return sorted(books, key=lambda b: b['priority'])


def match_market(fight, markets):
    wanted = {odds_identity(fight['red']), odds_identity(fight['blue'])}
    candidates = [m for m in markets if {odds_identity(n) for n in m['athletes'].values()} == wanted
                  and abs((datetime.fromisoformat(m['date'])-datetime.fromisoformat(fight['date'])).days) <= 1]
    return candidates[0] if len(candidates) == 1 else None


def forecast_open(fight, market=None, at=None):
    at = at or datetime.now(timezone.utc)
    if market and market.get('start_at'):
        return market.get('state') == 'pre' and at < timestamp(market['start_at'])
    # Without a trustworthy start time, freeze at UTC midnight on event day.
    return fight['date'] > at.date().isoformat()


def display_odds(fight, market, interval, feed_failed=False):
    if not market:
        return dict(available=False, message='No matching sportsbook market yet.')
    fetched = market.get('fetched_at')
    stale = not fetched or (datetime.now(timezone.utc)-timestamp(fetched)).total_seconds() > max(interval*2, 180)
    books = [dict(name=b['name'], red=b['prices'][odds_identity(fight['red'])],
                  blue=b['prices'][odds_identity(fight['blue'])], updated_at=b.get('updated_at')) for b in market.get('books', [])]
    return dict(available=bool(books), books=books, fetched_at=fetched, stale=stale or bool(market.get('error')) or feed_failed,
                error=market.get('error'), state=market.get('state'), start_at=market.get('start_at'),
                card_section=market.get('card_section'), section_source=market.get('section_source'),
                source='ESPN', source_url='https://www.espn.com/mma/fightcenter/_/id/'+market['event_id'],
                message='Moneylines not posted for this fight.' if not books else None)


class ESPNOdds:
    def __init__(self):
        self.session = requests.Session()
        self.session.mount('https://', HTTPAdapter(max_retries=Retry(total=1, backoff_factor=.5,
            status_forcelist=[429, 500, 502, 503, 504])))

    def schedule(self):
        today = datetime.now(timezone.utc)
        dates = (today-timedelta(days=1)).strftime('%Y%m%d')+'-'+(today+timedelta(days=120)).strftime('%Y%m%d')
        r = self.session.get(SCOREBOARD, params={'dates':dates, 'limit':100}, timeout=(10, 25))
        r.raise_for_status()
        return parse_schedule(r.json())

    def prices(self, bout):
        time.sleep(.15)
        url = f'{CORE}/events/{bout["event_id"]}/competitions/{bout["id"]}/odds'
        r = self.session.get(url, timeout=(10, 20))
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return parse_prices(r.json(), bout)
