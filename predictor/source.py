"""Polite, validated UFCStats scraping. Block pages never count as a successful sync."""
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .store import bout_id, identity, now

BASE = 'http://ufcstats.com'


def parse_events(html):
    soup = BeautifulSoup(html, 'html.parser')
    events = []
    for row in soup.select('tr'):
        link = row.select_one('a[href*="event-details/"]')
        date_node = row.select_one('.b-statistics__date')
        if not link or not date_node:
            continue
        day = datetime.strptime(date_node.get_text(' ', strip=True), '%B %d, %Y').date().isoformat()
        events.append(dict(name=link.get_text(' ', strip=True), date=day, url=link['href']))
    if not events:
        raise ValueError('UFCStats returned no recognizable events (possibly a browser challenge or changed page).')
    return events


def parse_card(html, event):
    soup = BeautifulSoup(html, 'html.parser')
    fights = []
    for row in soup.select('tr.b-fight-details__table-row'):
        links = row.select('a[href*="fighter-details/"]')
        if len(links) != 2:
            continue
        cells = row.select('td')
        result = cells[0].get_text(' ', strip=True).lower()
        tokens = set(result.split())
        winner = ('other' if tokens & {'draw', 'nc', 'd', 'n/c'} or 'no contest' in result
                  else 'red' if tokens & {'w', 'win'} else 'blue' if tokens & {'l', 'loss'} else None)
        red, blue = [link.get_text(' ', strip=True) for link in links]
        fights.append(dict(id=bout_id(event['date'], red, blue), date=event['date'], event=event['name'],
                           event_url=event['url'], red=red, blue=blue, red_url=links[0]['href'], blue_url=links[1]['href'],
                           winner=winner, weight_class=cells[6].get_text(' ', strip=True) if len(cells)>6 else '',
                           method=cells[7].get_text(' ', strip=True) if len(cells)>7 else '', source='UFCStats'))
    if not fights:
        if event['date'] > datetime.now().date().isoformat() and soup.select_one('table.b-fight-details__table'):
            return []  # An announced event can legitimately have no announced bouts yet.
        raise ValueError(f'No recognizable fights in {event["name"]}; previous data retained.')
    return fights


def parse_fighter(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    name = soup.select_one('.b-content__title-highlight')
    if not name:
        raise ValueError('Unrecognized UFCStats fighter profile')
    data = dict(name=name.get_text(' ', strip=True), url=url, source='UFCStats', fetched_at=now())
    mapping = {'Height':'height', 'Weight':'weight', 'Reach':'reach', 'STANCE':'stance', 'DOB':'dob',
               'SLpM':'SLpM', 'Str. Acc.':'sig_str_acc', 'SApM':'SApM', 'Str. Def':'str_def',
               'TD Avg.':'td_avg', 'TD Acc.':'td_acc', 'TD Def.':'td_def', 'Sub. Avg.':'sub_avg'}
    for li in soup.select('li.b-list__box-list-item'):
        text = li.get_text(' ', strip=True)
        if ':' not in text:
            continue
        label, value = (s.strip() for s in text.split(':', 1))
        key = mapping.get(label)
        if key:
            data[key] = value
    record = soup.select_one('.b-content__title-record')
    if record:
        data['record'] = record.get_text(' ', strip=True).replace('Record:', '').strip()
    if 'SLpM' not in data:
        raise ValueError('Fighter profile is missing statistics')
    return data


class UFCStats:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'MMAOraclePredictions/1.0 (personal UFC statistics dashboard)'
        self.session.mount('http://', HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504])))
        self.last_request = 0

    def get(self, url):
        parsed = urlparse(url)
        if parsed.hostname not in ('ufcstats.com', 'www.ufcstats.com') or parsed.scheme not in ('http', 'https'):
            raise ValueError('Only official UFCStats URLs are accepted')
        time.sleep(max(0, .6 - (time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.get(url, timeout=(10, 30))
        response.raise_for_status()
        if 'Checking your browser' in response.text or 'This site requires JavaScript' in response.text:
            return self.browser_get(url)
        return response.text

    def browser_get(self, url):
        # Let the site's normal JavaScript browser check run, then reuse its session.
        # No CAPTCHA solving or credentials are involved.
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    page.goto(url, timeout=45000)
                    page.wait_for_selector('a[href*="event-details/"], a[href*="fighter-details/"], .b-content__title-highlight', timeout=30000)
                    self.session.headers['User-Agent'] = page.evaluate('navigator.userAgent')
                    for cookie in context.cookies():
                        self.session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
                    return page.content()
                finally:
                    browser.close()
        except Exception as exc:
            raise ValueError('UFCStats browser check failed. Install Chromium with: python -m playwright install chromium. '+str(exc)[:150]) from exc

    def events(self):
        # The completed index also lists the next announced card.
        events = parse_events(self.get(BASE+'/statistics/events/completed?page=all'))
        # A failed upcoming index must not erase later cached cards.
        events += parse_events(self.get(BASE+'/statistics/events/upcoming?page=all'))
        return sorted({e['url']:e for e in events}.values(), key=lambda e:e['date'])

    def card(self, event):
        return parse_card(self.get(event['url']), event)

    def fighter(self, url):
        return parse_fighter(self.get(url), url)

    def find_fighter(self, name):
        parts = name.split()
        initials = {identity(parts[-1])[:1]}
        if parts[-1].lower().rstrip('.') in ('jr', 'sr') and len(parts)>1:
            initials.add(identity(parts[-2])[:1])
        for initial in sorted(initials):
            html = self.get(BASE+'/statistics/fighters?char='+initial+'&page=all')
            soup = BeautifulSoup(html, 'html.parser')
            for row in soup.select('tr'):
                links = row.select('a[href*="fighter-details/"]')
                if len(links)>=2 and identity(links[0].get_text(' ',strip=True)+' '+links[1].get_text(' ',strip=True))==identity(name):
                    return self.fighter(links[0]['href'])
        raise ValueError('No exact UFCStats profile match for '+name)
