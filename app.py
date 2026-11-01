import argparse
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request, abort, Response
from waitress import serve

from predictor.service import Service
from predictor.store import Store
from predictor.catalog import Catalog, ORIGIN

ROOT = Path(__file__).resolve().parent


def create_app(data_dir=None, bootstrap=True, auto_updates=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 4096
    store = Store(data_dir or os.environ.get('DATA_DIR', ROOT / 'data'))
    if bootstrap:
        store.bootstrap(ROOT)
    service = Service(store)
    if bootstrap and (service.bundle is None or store.meta('training_pending')):
        service.retrain()
    app.extensions['predictor'] = service
    if auto_updates if auto_updates is not None else bootstrap:
        service.start_background_updates()

    page_cache = {}
    def catalog():
        revision = service.update_revision()
        if page_cache.get('revision') != revision:
            page_cache.update(revision=revision, catalog=Catalog(service))
        return page_cache['catalog']

    @app.get('/')
    def index():
        return render_template('index.html', **catalog().common())

    @app.get('/fighters/')
    @app.get('/fighters/<slug>/')
    @app.get('/events/')
    @app.get('/events/<slug>/')
    @app.get('/fights/')
    @app.get('/fights/<slug>/')
    @app.get('/model/')
    @app.get('/status/')
    def public_page(slug=None):
        context = catalog().page(request.path)
        if context is None:
            abort(404)
        return render_template('detail.html', **context)

    @app.get('/robots.txt')
    def robots():
        return Response('User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: ' + ORIGIN + '/sitemap.xml\n', mimetype='text/plain')

    @app.get('/sitemap.xml')
    def sitemap():
        from xml.sax.saxutils import escape
        urls = ''.join('<url><loc>' + escape(ORIGIN + path) + '</loc></url>' for path in ['/'] + catalog().paths())
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + '</urlset>', mimetype='application/xml')

    @app.get('/api/dashboard')
    def dashboard():
        return jsonify(service.dashboard())

    @app.get('/api/update')
    def update():
        return jsonify(revision=service.update_revision())

    @app.get('/api/health')
    def api_health():
        d = service.dashboard()
        return jsonify(revision=d['revision'], last_sync=d['status']['last_sync'],
                       last_error=d['status']['last_error'], automatic=service.auto_updates,
                       upcoming_bouts=len(d['upcoming']), verified_bouts=d['total_fights'],
                       model_version=d['model']['version'] if d['model'] else None)

    @app.post('/api/matchup')
    def matchup():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ('red', 'blue')):
            return jsonify(error='Two fighter names are required.'), 400
        if service.bundle is None:
            return jsonify(error='Model is not ready.'), 503
        try:
            return jsonify(service.matchup(data['red'], data['blue']))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/health')
    def health():
        return jsonify(status='ok', model_ready=service.bundle is not None)

    @app.after_request
    def headers(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' https://www.gstatic.com; connect-src 'self' https://*.googleapis.com https://*.firebaseio.com https://*.google-analytics.com https://*.firebasestorage.app; img-src 'self' data: https://*.google-analytics.com https://*.espncdn.com; frame-ancestors 'none'"
        return response

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sync-once', action='store_true', help='Refresh data, retrain when results change, then exit')
    parser.add_argument('--no-sync', action='store_true', help='Serve cached data without network refresh')
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8000')))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    app = create_app(auto_updates=not (args.no_sync or args.sync_once))
    service = app.extensions['predictor']
    if args.sync_once:
        service.sync_odds()
        service.sync()
    else:
        print(f'MMA Cipher is running at http://{args.host}:{args.port}', flush=True)
        serve(app, host=args.host, port=args.port, threads=6)
