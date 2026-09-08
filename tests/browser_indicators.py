"""Offline browser regression checks for card indicators and responsive layout."""
from pathlib import Path
from flask import Flask, render_template
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
with app.test_request_context('/'):
    html=render_template('index.html')
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1100})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        path=r.request.url.split('http://oracle.test',1)[-1]
        if path=='/':r.fulfill(body=html,content_type='text/html')
        elif path.startswith('/static/'):
            file=root/path.split('?',1)[0].lstrip('/')
            r.fulfill(path=str(file))
        else:r.fulfill(status=503,body='offline')
    page.route('**/*',route)
    page.goto('http://oracle.test/',wait_until='networkidle')
    result=page.evaluate("""() => {
      const at=Date.now(); data={fighters:[],refresh:{odds_seconds:900}};
      const f={id:'test',red:'Fighter A',blue:'Fighter B',pick:'Fighter A',red_probability:.75,blue_probability:.25,confidence:.75,
        weight_class:"Women's Flyweight",date:'2099-01-01',model_version:'test',odds:{available:true,stale:false,state:'pre',
        start_at:new Date(at+3600000).toISOString(),fetched_at:new Date(at).toISOString(),books:[{name:'Book A',red:-150,blue:130},{name:'Book B',red:-125,blue:110}]}};
      const check=(ok,msg)=>{if(!ok)throw Error(msg)};
      check(modelEdge(f,at).name==='Book B','Best price selected');
      check(Math.abs(modelEdge(f,at).edge-(.75-125/225))<1e-9,'Break-even math');
      for(const patch of [{low_history:true},{locked:true},{prediction_pending:true},{red_probability:.69}])check(!modelEdge({...f,...patch},at),'Ineligible forecast');
      for(const patch of [{stale:true},{state:'in'},{error:'failed'},{fetched_at:new Date(at-3600000).toISOString()},{start_at:new Date(at-1).toISOString()},{books:[{name:'X',red:-1000,blue:600}]},{books:[{name:'X',red:0,blue:200}]}])check(!modelEdge({...f,odds:{...f.odds,...patch}},at),'Ineligible odds');
      const blue={...f,pick:f.blue,red_probability:.25,blue_probability:.75};
      check(modelEdge(blue,at).price===130,'Blue pick mapped correctly');
      check(cardSections([{...f,odds:{...f.odds,card_section:'main'}},{...f,odds:{...f.odds,card_section:'prelims'}}]).includes('Undercard / Prelims'),'Card grouping');
      check(cardSections([f]).includes('Card placement pending'),'Unknown placement');
      check(isWomensFight(f) && isWomensFight({...f,weight_class:'Women\u2019s Strawweight'}),'Women division detection');
      check(!isWomensFight({...f,weight_class:'Flyweight'}),'Men division unchanged');
      document.getElementById('cards').innerHTML='<div class="fight-grid">'+card(f)+card({...f,id:'men',weight_class:'Lightweight',odds:{...f.odds,stale:true}})+'</div>';
      return true;
    }""")
    assert result
    assert page.locator('.womens-fight.has-model-edge').count()==1
    assert page.locator('.model-edge').count()==1
    assert page.locator('.model-intro').is_visible()
    page.wait_for_timeout(300)
    page.screenshot(path=str(root/'test-results/indicators-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.locator('.model-intro').is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.wait_for_timeout(300)
    page.screenshot(path=str(root/'test-results/indicators-mobile.png'),full_page=True)
    assert not errors,errors
    browser.close()
print('Browser checks passed: badge thresholds, stale/live exclusions, corner mapping, division tint, desktop/mobile layout.')
