"""Stable public paths shared by JSON, Flask routes, and the static exporter."""
import re
import unicodedata


def slug(value):
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-') or 'unknown'


def fighter_path(name):
    return '/fighters/' + slug(name) + '/'


def event_path(fight):
    return '/events/' + fight['date'] + '-' + slug(fight.get('event') or 'UFC event') + '/'


def fight_path(fight):
    return '/fights/' + fight['date'] + '-' + slug(fight['red'] + '-vs-' + fight['blue']) + '-' + fight['id'][:8] + '/'
