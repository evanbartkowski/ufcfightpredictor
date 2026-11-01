"""Run against app.py --no-sync to verify automatic refresh and UI state."""
import json
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
payload = requests.get('http://127.0.0.1:8000/api/dashboard', timeout=20).json()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    calls = []
    fail = [False]
    def dashboard(route):
        calls.append(1)
        if fail[0]:
            route.abort()
        else:
            response = dict(payload, total_fights=payload['total_fights'] + len(calls), revision=str(len(calls)))
            route.fulfill(json=response)
    def update(route):
        if fail[0]:
            calls.append(1)
            route.abort()
        else:
            route.fulfill(json={'revision': str(len(calls) + 1)})
    page.route('**/api/dashboard?*', dashboard)
    page.route('**/api/update?*', update)
    page.clock.install()
    page.goto('http://127.0.0.1:8000', wait_until='networkidle')
    page.wait_for_function("() => document.getElementById('fight-count').textContent !== '—'")
    assert len(calls) == 1
    assert page.locator('details.undercard-dropdown').count()
    assert page.locator('details.undercard-dropdown[open]').count() == 0
    details = page.locator('details.undercard-dropdown').first
    details.locator('.undercard-summary').click()
    page.wait_for_function('() => openUndercardSections.size === 1')
    page.clock.fast_forward(65000)
    page.wait_for_function(f"() => document.getElementById('fight-count').textContent === '{payload['total_fights'] + 2:,}'")
    assert page.locator('details.undercard-dropdown[open]').count() == 1
    details.locator('.undercard-summary').click()
    page.wait_for_function('() => openUndercardSections.size === 0')
    page.locator('[data-filter="upset"]').click()
    assert page.locator('details.undercard-dropdown[open]').count() == 0
    page.locator('[data-filter="all"]').click()
    assert page.locator('details.undercard-dropdown[open]').count() == 0

    # A second event never inherits an identically labeled section's open state.
    details.locator('.undercard-summary').click()
    page.wait_for_function('() => openUndercardSections.size === 1')
    options = page.locator('#event-filter option').evaluate_all('(items) => items.map(x => x.value)')
    if len(options) > 2:
        page.select_option('#event-filter', options[2])
        assert page.locator('details.undercard-dropdown[open]').count() == 0
        page.select_option('#event-filter', options[1])
        assert page.locator('details.undercard-dropdown[open]').count() == 1

    # Fresh local cache must not suppress retries after a network failure.
    fail[0] = True
    page.clock.fast_forward(65000)
    page.get_by_text('Connection interrupted.', exact=False).wait_for()
    failed_calls = len(calls)
    fail[0] = False
    page.clock.fast_forward(35000)
    page.wait_for_function(f"() => document.getElementById('fight-count').textContent === '{payload['total_fights'] + failed_calls + 1:,}'")
    assert len(calls) == failed_calls + 1
    assert page.locator('.notice.warning').count() == 0

    # An unchanged tiny revision check must not download the full dashboard.
    before = len(calls)
    page.unroute('**/api/update?*', update)
    page.route('**/api/update?*', lambda route: route.fulfill(json={'revision': str(before)}))
    page.clock.fast_forward(65000)
    page.wait_for_function('() => !loading')
    assert len(calls) == before

    assert page.locator('.art-icon').first.evaluate('(svg) => svg.getBBox().width > 0')
    assert not page.evaluate(r'/[\u{1F300}-\u{1FAFF}]/u.test(document.body.innerText)')
    page.screenshot(path=str(ROOT / 'test-results' / 'updates-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(ROOT / 'test-results' / 'updates-mobile.png'))

    offline = browser.new_page()
    offline.on('pageerror', lambda error: errors.append(str(error)))
    old = dict(payload, upcoming=[dict(payload['upcoming'][0], date='2026-10-17', event='Expired October card')])
    offline.add_init_script('localStorage.setItem("mma_cipher_dashboard", ' + json.dumps(json.dumps(old)) + '); localStorage.setItem("mma_cipher_last_pull", String(Date.now()));')
    offline.route('**/api/dashboard?*', lambda route: route.abort())
    offline.goto('http://127.0.0.1:8000', wait_until='networkidle')
    offline.get_by_text('Connection interrupted.', exact=False).wait_for()
    # Current pre-rendered HTML remains useful even if an old browser cache and
    # an interrupted request are both present.
    assert offline.locator('#card-count').inner_text() == str(len(payload['upcoming']))
    assert 'Expired October card' not in offline.locator('#cards').inner_text()
    assert not errors, errors
    browser.close()

print('Browser updates passed: automatic polling, network retry, stale-cache filtering, undercard state, SVG artwork, desktop and mobile.')
