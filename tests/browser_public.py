"""Verify crawler-visible content, accessible records, and browser-local picks."""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(java_script_enabled=False, viewport={'width': 1440, 'height': 1050})
    page.goto('http://127.0.0.1:8000', wait_until='networkidle')
    assert int(page.locator('#card-count').inner_text()) > 0
    assert 'Not yet' not in page.locator('#last-sync').inner_text()
    assert page.locator('#cards article').count() > 0
    assert page.locator('details.undercard-dropdown[open]').count() == 0
    page.locator('#cards a[href^="/fights/"]').first.click()
    assert page.get_by_role('heading', name='Evidence and uncertainty').is_visible()
    assert 1 <= page.locator('.evidence-list li').count() <= 3
    assert 'Major uncertainty' in page.locator('body').inner_text()
    page.locator('a[href^="/fighters/"]').filter(has_text='profile').first.click()
    assert page.get_by_role('heading', name='UFC opponent history').is_visible()
    assert page.locator('.elo-chart polyline').count() == 1
    page.screenshot(path=str(ROOT / 'test-results/public-fighter-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(ROOT / 'test-results/public-fighter-mobile.png'))

    live = browser.new_page(viewport={'width': 1440, 'height': 1050})
    errors = []
    live.on('pageerror', lambda error: errors.append(str(error)))
    live.goto('http://127.0.0.1:8000', wait_until='networkidle')
    live.wait_for_selector('.expand-card-btn')
    card = live.locator('.fight-card').first
    card.locator('.expand-card-btn').click()
    picks = card.locator('.user-pick-container')
    assert 'YOUR PERSONAL PICK' in picks.inner_text()
    assert 'Saved on this browser' in picks.inner_text()
    assert 'COMMUNITY' not in picks.inner_text()
    assert picks.locator('[role="progressbar"]').count() == 0
    red = picks.locator('[data-pick-side="red"]')
    red.click()
    assert picks.locator('[data-pick-side="red"]').get_attribute('aria-pressed') == 'true'
    red.click()
    assert picks.locator('[data-pick-side="red"]').get_attribute('aria-pressed') == 'false'
    red.click()
    live.reload(wait_until='networkidle')
    live.locator('.fight-card').first.locator('.expand-card-btn').click()
    assert live.locator('.fight-card').first.locator('[data-pick-side="red"]').get_attribute('aria-pressed') == 'true'
    live.get_by_role('button', name='Inside the Algorithm', exact=True).click()
    assert 'not been separately calibrated' in live.locator('#model-info').inner_text()
    assert 'Nelder-Mead' not in live.locator('#model-info').inner_text()
    assert not errors, errors
    browser.close()

print('Public pages passed: no-JavaScript card, evidence, fighter history and Elo, desktop/mobile layout, personal-pick persistence, and honest model metadata.')
