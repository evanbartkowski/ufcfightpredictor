import argparse
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request, abort, Response, g
from waitress import serve

from predictor.service import Service
from predictor.store import Store, identity
from predictor.links import fighter_path
from predictor.catalog import Catalog, ORIGIN
from predictor.api_views import overview, prediction_page, analytics_page
from predictor.limits import MatchupLimiter
from predictor.prebuilt import install_prebuilt

ROOT = Path(__file__).resolve().parent


def create_app(data_dir=None, bootstrap=True, auto_updates=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 4096
    directory = data_dir or os.environ.get('DATA_DIR', ROOT / 'data')
    if bootstrap:
        install_prebuilt(directory)
    store = Store(directory)
    if bootstrap:
        store.bootstrap(ROOT)
    service = Service(store)
    app.extensions['predictor'] = service
    limiter = MatchupLimiter()
    app.extensions['matchup_limiter'] = limiter

    @app.before_request
    def connection_scope():
        if request.path in ('/health','/ready'):
            return
        g.store_scope = store.scope()
        g.store_scope.__enter__()

    @app.teardown_request
    def close_connection(error):
        scope = g.pop('store_scope',None)
        if scope:
            scope.__exit__(type(error) if error else None,error,error.__traceback__ if error else None)
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
        return Response(service.dashboard_bytes(),mimetype='application/json')

    small_views = {}
    @app.get('/api/overview')
    def api_overview():
        data = service.dashboard()
        if small_views.get('revision') != data['revision']:
            small_views.update(revision=data['revision'], data=app.json.dumps(overview(data),separators=(',',':')).encode())
        return Response(small_views['data'],mimetype='application/json')

    def paging(default=48):
        try:
            number, size = int(request.args.get('page','1')), int(request.args.get('per_page',str(default)))
            if not 1 <= number <= 10000 or not 1 <= size <= 100:
                raise ValueError()
            return number,size
        except ValueError:
            abort(400,description='page and per_page must be positive bounded integers')

    @app.get('/api/fighters')
    def api_fighters():
        number,size = paging()
        query,division = request.args.get('q','').strip(), request.args.get('division','').strip()
        if len(query)>100 or len(division)>100:
            abort(400)
        result = store.fighter_page(query,division,number,size)
        histories = store.recent_bouts(f['name'] for f in result['items'])
        # Enrich only this bounded page, using indexed fighter-bout queries.
        for fighter in result['items']:
            fighter['path'] = fighter_path(fighter['name'])
            history = histories.get(identity(fighter['name']),dict(items=[],total=0))
            fighter['total_recorded_bouts'] = history['total']
            fighter['recent_bouts'] = []
            for bout in history['items']:
                corner = 'red' if identity(bout['red'])==identity(fighter['name']) else 'blue'
                fighter['recent_bouts'].append(dict(date=bout['date'],event=bout.get('event'),method=bout.get('method'),
                    opponent=bout['blue' if corner=='red' else 'red'],result='W' if bout.get('winner')==corner else 'L' if bout.get('winner') in ('red','blue') else 'NC/D'))
        result['pages'] = max(1,(result['total']+size-1)//size)
        return jsonify(result)

    @app.get('/api/fighters/<name>')
    def api_fighter(name):
        fighter = store.fighters_by_names((name,)).get(identity(name))
        if fighter is None:
            abort(404)
        return jsonify(fighter)

    @app.get('/api/fighters/<name>/history')
    def api_fighter_history(name):
        number,size = paging()
        if not store.fighters_by_names((name,)):
            abort(404)
        return jsonify(store.fighter_history(name,number,size))

    @app.get('/api/predictions')
    def api_predictions():
        number,size = paging()
        kind = request.args.get('kind','upcoming')
        if kind not in ('upcoming','results'):
            abort(400)
        return jsonify(prediction_page(service.dashboard(),kind,number,size))

    @app.get('/api/analytics')
    def api_analytics():
        number,size = paging(100)
        kind = request.args.get('kind','events')
        if kind not in ('events','holdout'):
            abort(400)
        if service.bundle is None:
            return jsonify(error='Model is not ready'),503
        return jsonify(analytics_page(service.bundle,kind,number,size))

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
        if not limiter.allow(request.remote_addr or 'unknown'):
            response = jsonify(error='Too many matchup requests. Try again shortly.')
            response.headers['Retry-After'] = '60'
            return response,429
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and 0 < len(data[k].strip()) <= 100 for k in ('red', 'blue')):
            return jsonify(error='Two fighter names are required.'), 400
        if service.bundle is None:
            return jsonify(error='Model is not ready.'), 503
        if not limiter.concurrent.acquire(blocking=False):
            response = jsonify(error='Matchup calculations are busy. Try again shortly.')
            response.headers['Retry-After'] = '1'
            return response,429
        try:
            return jsonify(service.matchup(data['red'], data['blue']))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        finally:
            limiter.concurrent.release()

    @app.get('/health')
    def health():
        return jsonify(status='ok', model_ready=service.bundle is not None)

    @app.get('/ready')
    def ready():
        try:
            with store.connect() as db:
                db.execute('SELECT version FROM revisions LIMIT 1').fetchone()
            ready = service.bundle is not None
        except Exception:
            ready = False
        return jsonify(status='ready' if ready else 'unavailable',model_ready=service.bundle is not None),200 if ready else 503

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
    parser.add_argument('--prepare-model', action='store_true', help='Explicitly seed and train serving state, then exit')
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8000')))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    app = create_app(auto_updates=not (args.no_sync or args.sync_once or args.prepare_model))
    service = app.extensions['predictor']
    if args.prepare_model:
        if service.bundle is None or service.store.meta('training_pending'):
            service.retrain()
    elif args.sync_once:
        if service.bundle is None:
            service.retrain()
        service.sync_odds()
        service.sync()
    else:
        print(f'MMA Cipher is running at http://{args.host}:{args.port}', flush=True)
        serve(app, host=args.host, port=args.port, threads=6)
