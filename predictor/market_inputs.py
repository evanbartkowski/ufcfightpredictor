"""Only timestamped, pair-matched pre-event prices qualify for odds-aware research."""
from datetime import timedelta
import math

from .odds import moneyline, odds_identity
from .profiles import parse_time


def verified_market(fight):
    snapshot = fight.get('market_snapshot') or {}
    observed, start = parse_time(snapshot.get('observed_at')), parse_time(snapshot.get('start_at'))
    if not observed or not start or not observed < start or start - observed > timedelta(hours=24):
        return None
    if snapshot.get('fight_date') != fight['date'] or abs((start.date() - parse_time(fight['date']+'T00:00:00+00:00').date()).days) > 1:
        return None
    prices = snapshot.get('prices') or {}
    wanted = {odds_identity(fight['red']), odds_identity(fight['blue'])}
    if set(prices) != wanted:
        return None
    red, blue = [moneyline(prices.get(odds_identity(fight[corner]))) for corner in ('red','blue')]
    return (red, blue) if red is not None and blue is not None else None


def market_features(fight):
    prices = verified_market(fight)
    if prices is None:
        return (0., 0.)
    implied = lambda price: 100/(price+100) if price > 0 else -price/(-price+100)
    red, blue = map(implied, prices)
    p = min(.99, max(.01, red/(red+blue)))
    return p - .5, math.log(p/(1-p))
