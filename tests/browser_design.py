"""Check shared page styling, real portraits, directories, and narrow layouts."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get('DESIGN_TEST_URL', 'http://127.0.0.1:8000').rstrip('/')
OUT = Path(__file__).resolve().parents[1] / 'test-results'
OUT.mkdir(exist_ok=True)


def check_width(page, label):
    diagnostics = page.evaluate('''() => ({
      viewport:innerWidth, content:document.documentElement.scrollWidth,
      elements:[...document.querySelectorAll('body *')].filter(e => {
        const rect=e.getBoundingClientRect();
        return rect.width>0 && rect.right>innerWidth+1 && !e.closest('.tabs,.event-shelf-track,.site-navigation,.table-scroll');
      }).slice(0,12).map(e=>({tag:e.tagName,class:String(e.className),right:e.getBoundingClientRect().right}))
    })''')
    assert diagnostics['content'] <= diagnostics['viewport'], (label,diagnostics)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1050})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE + '/', wait_until='networkidle')
    page.wait_for_selector('#cards .fighter-img')
    assert page.locator('header .site-navigation a').count() == 2
    assert page.locator('.library-shortcut:visible').count() == 0
    assert page.locator('a[href="/status/"]').count() == 0
    assert page.get_by_role('button',name='Model Watch',exact=False).is_visible()
    assert page.locator('.upset-radar-banner,.is-upset-card').count() == 0
    assert page.locator('.public-nav, .published-events').count() == 0
    assert page.locator('.event-shelf .event-tile').count() > 0
    assert page.locator('.event-shelf').bounding_box()['y'] < page.locator('#cards').bounding_box()['y']
    assert page.locator('details.undercard-dropdown[open]').count() == 0
    page.locator('#cards .fighter-img').first.scroll_into_view_if_needed()
    page.wait_for_function("() => [...document.querySelectorAll('#cards .fighter-img')].filter(i => i.complete && i.naturalWidth > 0).length >= 4")
    event_path = page.locator('.event-shelf .event-tile').first.get_attribute('href')
    fighter_path = page.evaluate('data.upcoming[0].red_path')
    fight_path = page.evaluate('data.upcoming[0].path')
    page.screenshot(path=str(OUT / 'design-fight-card-desktop.png'), full_page=False)
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(OUT / 'design-home-desktop.png'), full_page=False)
    for width in (390,320,768):
        page.set_viewport_size({'width':width,'height':844})
        check_width(page, ('homepage',width))
    # Linux and Windows use different system fonts. Also exercise wider metrics.
    wider_font = page.add_style_tag(content='body * {font-family:Arial!important}')
    page.set_viewport_size({'width':320,'height':844})
    check_width(page, 'homepage with Arial')
    wider_font.evaluate('(element) => element.remove()')
    page.set_viewport_size({'width':390,'height':844})
    page.locator('.event-shelf').scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / 'design-home-schedule-mobile.png'))

    page.get_by_role('button',name='Fighter database',exact=False).click()
    assert page.locator('.library-shortcut:visible').count() == 4
    page.locator('#archive-query').fill('Waldo')
    page.wait_for_selector('.archive-result')
    page.locator('[data-archive-kind="forecasts"]').click()
    page.wait_for_function("() => document.querySelectorAll('.archive-result').length > 0 && [...document.querySelectorAll('.archive-result small')].every(e=>e.textContent==='ISSUED FORECAST')")
    page.screenshot(path=str(OUT / 'design-library-search-mobile.png'))
    page.reload(wait_until='networkidle')
    assert page.locator('#fighters').is_visible()

    for route in ['/events/','/fighters/','/fights/','/model/','/status/',event_path,fighter_path,fight_path]:
        page.set_viewport_size({'width':1440,'height':1050})
        response = page.goto(BASE + route, wait_until='networkidle')
        assert response.status == 200, route
        assert page.locator('h1').count() == 1, route
        assert page.locator('header [aria-current="page"]').inner_text() == 'Fighter database', route
        assert page.locator('header a[href="/status/"]').count() == 0
        assert page.locator('.page-hero').is_visible()
        page.screenshot(path=str(OUT / ('design-' + route.strip('/').replace('/','-') + '-desktop.png')))
        for width in (390, 320, 768):
            page.set_viewport_size({'width':width,'height':844})
            check_width(page, (route,width))
        if route in ('/events/','/fighters/',fighter_path):
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(OUT / ('design-' + route.strip('/').replace('/','-') + '-mobile.png')))

    page.goto(BASE + '/fighters/',wait_until='networkidle')
    first = page.locator('.fighter-tile').first.inner_text()
    page.locator('#directory-next').click()
    assert page.locator('.fighter-tile').first.inner_text() != first
    page.get_by_role('searchbox',name='Search fighter profiles').fill('Waldo Cortes Acosta')
    assert page.locator('.fighter-tile').count() == 1
    page.wait_for_function('() => document.querySelector(".fighter-tile img")?.naturalWidth > 0')
    page.locator('.fighter-tile').click()
    assert page.get_by_role('heading',name='UFC opponent history').is_visible()
    page.locator('.page-hero .portrait img').wait_for()
    page.wait_for_function('() => document.querySelector(".page-hero .portrait img").naturalWidth > 0')
    page.evaluate("document.querySelector('.page-hero .portrait img').src='/unavailable-portrait.png'")
    page.locator('.page-hero .portrait img.portrait-error').wait_for(state='attached')
    assert page.locator('.page-hero .portrait-initials').is_visible()

    page.goto(BASE + '/events/',wait_until='networkidle')
    page.get_by_role('searchbox',name='Search event cards').fill('UFC 334')
    assert page.locator('#upcoming-directory > div:visible').count() > 0
    page.get_by_role('searchbox',name='Search event cards').fill('2026-10-17')
    assert page.locator('#event-archive > div:visible').count() > 0
    assert all('2026' in text for text in page.locator('#event-archive > div:visible').all_inner_texts())
    page.get_by_role('searchbox',name='Search event cards').fill('no-matching-ufc-event')
    assert page.locator('[data-empty-for="event-archive"]').is_visible()
    page.goto(BASE + '/fights/',wait_until='networkidle')
    page.get_by_role('searchbox',name='Search issued forecasts').fill('Waldo')
    assert page.locator('#forecast-directory > div:visible').count() > 0
    page.locator('[data-record-filter="settled"]').click()
    assert page.locator('#forecast-directory > div:visible').evaluate_all('(items)=>items.every(e=>e.dataset.recordState==="settled")')
    assert not errors, errors
    browser.close()

print('Design checks passed: database-only archive links, cross-directory search, Model Watch without card banners, roster/event/forecast controls, portraits, and desktop/mobile/tablet layouts.')
