"""Manual browser check: start the server, then python tests/browser_smoke.py."""
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path('test-results')
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1450}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8000', wait_until='networkidle')
    page.wait_for_function("document.getElementById('fight-count').textContent !== '—'")
    assert page.title() == 'MMA Oracle Predictions — UFC predictions'
    dashboard = page.request.get('http://127.0.0.1:8000/api/dashboard')
    assert dashboard.headers['cache-control'] == 'no-store'
    assert page.locator('#odds-sync').is_visible()
    live = dashboard.json()
    assert live['refresh']['odds_seconds'] == 900
    assert live['refresh']['stats_seconds'] == 3600
    if live['upcoming'] and live['upcoming'][0]['odds'].get('available'):
        expected = live['upcoming'][0]['odds']['books'][0]
        prices = page.locator('.fight-card').first.locator('.odds-prices strong')
        assert prices.nth(0).inner_text() == (('+' if expected['red']>0 else '')+str(expected['red']))
        assert prices.nth(1).inner_text() == (('+' if expected['blue']>0 else '')+str(expected['blue']))
    page.screenshot(path=str(out/'dashboard-desktop.png'))
    first_event = page.locator('#event-filter').input_value()
    first_count = page.locator('.fight-card').count()
    page.locator('#event-filter').select_option('all')
    assert page.locator('.fight-card').count() >= first_count
    page.locator('#event-filter').select_option(first_event)
    if page.locator('.fight-card').count():
        page.get_by_text('Compare current fighter stats', exact=True).first.click()
        assert page.locator('.profile').first.is_visible()
        awaitable = page.evaluate('load()')
        assert page.locator('.profile').first.is_visible()
    page.get_by_role('button', name='Matchup lab', exact=True).click()
    page.locator('#red').fill('Max Holloway')
    page.locator('#blue').fill('Conor McGregor')
    page.get_by_role('button', name='Run prediction').click()
    page.locator('#matchup-output .pick').wait_for()
    assert page.locator('#matchup-output .profile').count() == 2
    page.screenshot(path=str(out/'matchup-desktop.png'), full_page=True)
    page.locator('#blue').fill('Max Holloway')
    page.get_by_role('button', name='Run prediction').click()
    page.get_by_text('Choose two different fighters.',exact=True).wait_for()
    page.get_by_role('button', name='Prediction record',exact=True).click()
    assert page.locator('#results').is_visible()
    page.get_by_role('button', name='Inside the model',exact=True).click()
    assert page.get_by_text('Brier score · lower is better',exact=True).is_visible()
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button', name='Upcoming fights').click()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(out/'dashboard-mobile.png'))
    assert not errors, errors
    browser.close()
print('Browser smoke checks passed: desktop, mobile, profiles, predictions, errors, and model metrics.')
