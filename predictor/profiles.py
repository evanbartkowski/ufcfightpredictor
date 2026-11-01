"""Typed profile inputs and timestamp eligibility, kept separate from raw display data."""
import math
import re
from datetime import date, datetime, timezone


def number(value):
    try:
        value = float(str(value).strip().replace(',', ''))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def percentage(value):
    result = number(str(value).strip().rstrip('%'))
    if result is None:
        return None
    if '%' in str(value) or result > 1:
        result /= 100
    return result if 0 <= result <= 1 else None


def centimetres(value):
    text = str(value or '').strip().lower()
    feet = re.fullmatch(r'''(\d+)\s*'\s*(\d+(?:\.\d+)?)\s*["\u2033]?''', text)
    if feet:
        result = (float(feet[1]) * 12 + float(feet[2])) * 2.54
    elif '"' in text or 'in' in text or '\u2033' in text:
        result = number(re.sub(r'[^\d.]', '', text))
        result = result * 2.54 if result is not None else None
    else:
        result = number(text.replace('cm', '').strip())
    return result if result is not None and 50 <= result <= 260 else None


def birthdate(value):
    for fmt in ('%Y-%m-%d', '%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def normalize_profile(profile):
    result = {key: centimetres(profile.get(key)) for key in ('height', 'reach')}
    weight = number(re.sub(r'[^\d.]', '', str(profile.get('weight', ''))))
    if weight is not None and 'kg' in str(profile.get('weight', '')).lower():
        weight *= 2.2046226218
    result['weight'] = weight if weight is not None and 70 <= weight <= 400 else None
    result['dob'] = birthdate(profile.get('dob'))
    result['stance'] = str(profile.get('stance') or '').strip() or None
    for source, target in [('SLpM','sig_str_landed'), ('SApM','sig_str_absorbed'),
                           ('td_avg','td_landed'), ('sub_avg','sub_att')]:
        value = number(profile.get(source))
        result[target] = value if value is not None and 0 <= value <= 100 else None
    for source, target in [('sig_str_acc','sig_str_pct'), ('str_def','str_def'),
                           ('td_acc','td_pct'), ('td_def','td_def')]:
        result[target] = percentage(profile.get(source))
    return {key: value for key, value in result.items() if value is not None}


def parse_time(value):
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return at if at.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def live_inputs(fight, profiles, at=None, start_at=None):
    """Only merge observations known before issue time and before event start.

    Historical replay never calls this function or reads the current profiles.
    Raw profiles remain intact for source-faithful display.
    """
    at = at or datetime.now(timezone.utc)
    boundary = parse_time(start_at) or datetime.fromisoformat(fight['date']).replace(tzinfo=timezone.utc)
    meta = dict(fight)
    provenance = {}
    for corner in ('red', 'blue'):
        profile = profiles.get(corner) or {}
        fetched = parse_time(profile.get('fetched_at'))
        eligible = fetched is not None and fetched <= at and fetched < boundary
        values = normalize_profile(profile) if eligible else {}
        if values.get('dob'):
            dob = date.fromisoformat(values.pop('dob'))
            event_day = date.fromisoformat(fight['date'])
            age = event_day.year - dob.year - ((event_day.month, event_day.day) < (dob.month, dob.day))
            if 16 <= age <= 70:
                values['age'] = age
        for key, value in values.items():
            meta[corner + '_' + key] = value
        provenance[corner] = dict(eligible=eligible, fetched_at=profile.get('fetched_at'), fields=sorted(values))
    meta['input_provenance'] = dict(profiles=provenance, market_policy='market-free')
    return meta
