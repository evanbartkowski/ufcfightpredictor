"""Rasterize the project's own vector branding for browsers and link previews."""
import base64
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def main():
    mark=(STATIC/'cipher-mark.svg').read_text(encoding='utf-8')
    font=base64.b64encode((STATIC/'fonts/barlow-condensed-latin.woff2').read_bytes()).decode()
    mono=base64.b64encode((STATIC/'fonts/ibm-plex-mono-latin.woff2').read_bytes()).decode()
    (STATIC/'icons').mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(device_scale_factor=1)
        for size,name in [(96,'icon-96.png'),(180,'apple-touch-icon.png'),(192,'icon-192.png'),(512,'icon-512.png')]:
            page.set_viewport_size(dict(width=size,height=size))
            page.set_content('<style>body{margin:0;background:#030507;display:grid;place-items:center;height:100vh}svg{width:88%;height:88%}</style>'+mark)
            page.screenshot(path=str(STATIC/'icons'/name))
        page.set_viewport_size(dict(width=1200,height=630))
        html="""<style>
        @font-face{font-family:display;src:url(data:font/woff2;base64,FONT)}
        @font-face{font-family:mono;src:url(data:font/woff2;base64,MONO)}
        *{box-sizing:border-box}body{margin:0;background:#030507;color:#f2f6fa;font-family:display}
        .frame{height:630px;padding:50px 60px;position:relative;overflow:hidden;border:1px solid #263746;background:radial-gradient(ellipse at 90% 10%,#27101e,transparent 65%),#030507}
        .logo{display:flex;align-items:center;gap:18px;font-size:34px;letter-spacing:3px}.logo svg{width:64px;height:64px}.eyebrow{font:12px mono;color:#62dbe7;letter-spacing:3px;margin:40px 0 14px}
        h1{font-size:105px;line-height:.9;font-weight:700;margin:0;letter-spacing:1px}h1 span{color:#ff4162}
        p{font:14px mono;color:#a2b2c7;margin-top:24px}.url{position:absolute;bottom:34px;left:60px;font:12px mono;color:#6e8fa3;letter-spacing:1px}
        .cage{position:absolute;right:-100px;top:150px;width:480px;height:400px;transform:rotate(-18deg);opacity:.45}.ring{position:absolute;inset:35px;border:1px solid #45c7d8;clip-path:polygon(29% 0,71% 0,100% 29%,100% 71%,71% 100%,29% 100%,0 71%,0 29%);background:repeating-linear-gradient(45deg,transparent 0 23px,#2d637344 24px,transparent 25px),repeating-linear-gradient(-45deg,transparent 0 23px,#2d637344 24px,transparent 25px)}.ring:nth-child(2){inset:65px;border-color:#ff4162}.ring:nth-child(3){inset:95px}
        </style><div class="frame"><div class="cage"><div class="ring"></div><div class="ring"></div><div class="ring"></div></div><div class="logo">MARK<span>MMA CIPHER</span></div><div class="eyebrow">MMACIPHER / UFC FIGHT INTELLIGENCE</div><h1>UFC PREDICTIONS.<br><span>FOLLOW THE EVIDENCE.</span></h1><p>Fighter records · Event cards · Published forecasts</p><div class="url">mma-oracle.web.app</div></div>"""
        page.set_content(html.replace('FONT',font).replace('MONO',mono).replace('MARK',mark))
        page.evaluate('document.fonts.ready')
        page.screenshot(path=str(STATIC/'social-preview.png'))
        browser.close()
    print('Rendered four browser icons and a 1200 x 630 social preview.')


if __name__=='__main__':main()
