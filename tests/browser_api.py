"""Lazy API payloads, pagination, cache migration, and actual interactive controls."""
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1050})
    errors=[];requests=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda request:requests.append(request.url))
    old=page.request.get('http://127.0.0.1:8000/api/dashboard').json()
    page.add_init_script('''if(!localStorage.getItem('migration_fixture')) {
      localStorage.setItem('mma_oracle_dashboard', '''+json.dumps(json.dumps(old))+''');
      localStorage.setItem('mma_oracle_last_pull',String(Date.now()));
      localStorage.setItem('migration_fixture','1');
    }''')
    page.goto('http://127.0.0.1:8000',wait_until='networkidle')
    assert any('/api/overview?' in url for url in requests)
    assert not any('/api/dashboard?' in url for url in requests)
    assert not any('/api/analytics?' in url or '/api/fighters?' in url for url in requests)
    assert page.evaluate("localStorage.getItem('mma_oracle_dashboard') === null")
    assert page.evaluate("localStorage.getItem('mma_oracle_last_pull') === null")
    assert page.evaluate("localStorage.getItem('mma_cipher_dashboard').length < 800000")
    page.get_by_role('button',name='Fighter database',exact=False).click()
    page.wait_for_function("() => document.getElementById('fighter-grid').querySelectorAll('.fighter-db-card').length === 48")
    page.wait_for_function('() => loadedFighterPages.size > 0')
    first=page.locator('.db-card-name').first.inner_text()
    page.locator('#fighter-next').click()
    assert first!=page.locator('.db-card-name').first.inner_text()
    page.locator('#fighter-search').fill('Max Holloway')
    page.wait_for_function("() => getFighterData('Max Holloway')?.SLpM !== undefined")
    assert page.locator('.fighter-db-card').count()==1
    page.get_by_role('button',name='AI Analytics',exact=True).click()
    page.wait_for_function('() => (data.model.metrics.events_history || []).length > 0')
    assert any('/api/analytics?' in url for url in requests)
    assert page.locator('#accuracy-chart-container svg').is_visible()
    page.get_by_role('button',name='Matchup lab',exact=True).click()
    page.locator('#red').fill('Max Holloway');page.locator('#blue').fill('Conor McGregor')
    page.get_by_role('button',name='Run prediction').click()
    page.locator('#matchup-output .pick').wait_for()
    assert page.locator('#matchup-output .profile').count()==2
    assert page.evaluate("getFighterData('Conor McGregor').SLpM !== undefined")
    assert page.evaluate("""() => {
      const indexed={name:'Fresh Profile',fetched_at:'2026-11-01T12:00:00Z',SLpM:'6.0'};
      data.fighters.push(indexed);
      data.upcoming.push({red:'Fresh Profile',blue:'Other',fighter_stats:{red:{name:'Fresh Profile',fetched_at:'2026-10-01T12:00:00Z',SLpM:'3.0'}}});
      const value=getFighterData('Fresh Profile').SLpM;
      data.fighters.pop();data.upcoming.pop();
      return value==='6.0';
    }""")
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('img:visible').evaluate_all('(imgs) => imgs.every(img => img.complete && img.naturalWidth > 0)')
    assert not errors,errors
    browser.close()
print('API browser checks passed: small initial payload, lazy profiles/analytics, fighter pagination/search, one-time cache migration, matchup inputs, images, and mobile layout.')
