from bs4 import BeautifulSoup
import pytest
from predictor.catalog import Catalog
from predictor.discovery import update_state
from predictor.discovery import robots_text
from predictor.links import fighter_path
from scripts.submit_indexnow import changed_paths, submit
from test_public_pages import public_app


def test_homepage_has_brand_alias_graph_and_sharing_metadata(public_app):
    import json
    html=BeautifulSoup(public_app.test_client().get('/').data,'html.parser')
    assert 'MMACipher' in html.title.text
    assert len(html.select('link[rel="canonical"]'))==1
    graph=json.loads(html.select_one('script[type="application/ld+json"]').string)['@graph']
    site=next(item for item in graph if item['@type']=='WebSite')
    assert site['name']=='MMA Cipher' and 'MMACipher' in site['alternateName']
    assert html.select_one('meta[name="twitter:card"]')['content']=='summary_large_image'
    assert html.select_one('meta[property="og:image"]')['content'].endswith('/social-preview.png')


def test_crawl_dates_ignore_refresh_times_and_change_for_real_profile_updates(public_app):
    service=public_app.extensions['predictor']
    first=update_state(Catalog(service),{},'2026-11-01T12:00:00Z')
    service.store.set_meta('last_sync','2026-11-01T12:15:00Z')
    same=update_state(Catalog(service),first,'2026-11-01T12:15:00Z')
    assert same==first
    profile=service.store.fighters_by_names(['Alice'])['alice']
    service.store.save_fighter(dict(profile,fetched_at='2026-11-01T12:20:00Z'))
    assert update_state(Catalog(service),first,'2026-11-01T12:20:00Z')==first
    service.store.save_fighter(dict(profile,record='11-3-0'))
    changed=update_state(Catalog(service),first,'2026-11-01T12:25:00Z')
    assert changed['pages'][fighter_path('Alice')]['lastmod']=='2026-11-01T12:25:00Z'
    assert changed['pages'][fighter_path('Beth')]==first['pages'][fighter_path('Beth')]
    assert '/status/' not in first['pages']


def test_html_record_index_links_to_profiles_and_preserves_utility_noindex(public_app):
    client=public_app.test_client()
    index=BeautifulSoup(client.get('/sitemap/').data,'html.parser')
    assert index.select_one('a[href="'+fighter_path('Alice')+'"]')
    status=BeautifulSoup(client.get('/status/').data,'html.parser')
    assert status.select_one('meta[name="robots"]')['content']=='noindex,follow'
    assert client.get('/about/').status_code==200


def test_indexnow_submits_changes_and_deletions_only():
    assert changed_paths({'/':{'hash':'new'},'/same/':{'hash':'same'}},{'/':'old','/same/':'same','/removed/':'gone'})==['/','/removed/']


def test_crawlers_can_render_public_pages_without_crawling_bulk_state():
    from urllib.robotparser import RobotFileParser
    parser=RobotFileParser();parser.parse(robots_text().splitlines())
    for bot in ('Googlebot','bingbot'):
        assert parser.can_fetch(bot,'https://mma-oracle.web.app/api/overview?_t=123')
        assert parser.can_fetch(bot,'https://mma-oracle.web.app/arena.js')
        assert parser.can_fetch(bot,'https://mma-oracle.web.app/fighters/alice/')
        assert not parser.can_fetch(bot,'https://mma-oracle.web.app/api/sync-state.json.gz')


def test_failed_indexnow_request_does_not_advance_submission_state(tmp_path):
    import json
    from pathlib import Path
    key=json.loads((Path(__file__).resolve().parents[1]/'static/indexnow.json').read_text())['key']
    class Response:
        text=key
        status_code=429
        def raise_for_status(self):pass
        def json(self):return {'pages':{'/':{'hash':'new'}}}
    class Session:
        def get(self,*args,**kwargs):return Response()
        def post(self,*args,**kwargs):return Response()
    state=tmp_path/'state.json';state.write_text('{"/":"old"}')
    with pytest.raises(RuntimeError,match='429'):submit('https://mma-oracle.web.app',state,Session())
    assert json.loads(state.read_text())=={'/':'old'}
