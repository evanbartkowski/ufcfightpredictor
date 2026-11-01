"""Render the same public content for Flask requests and Firebase publications."""
from pathlib import Path
import json
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .catalog import Catalog
from .publication import atomic_write
from .discovery import indexable, robots_text, sitemap_bytes

ROOT = Path(__file__).resolve().parents[1]


def static_url(endpoint, filename, **query):
    if endpoint != 'static':
        raise ValueError('Only static assets belong in the Hosting export')
    return '/' + filename + ('?' + urlencode(query) if query else '')


def export_pages(service, directory=None, dashboard=None):
    directory = Path(directory or ROOT / 'static')
    catalog = Catalog(service, dashboard)
    atomic_write(directory / 'api/search-index.json', json.dumps(catalog.search_index(),separators=(',',':')).encode())
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=select_autoescape(['html']),
                      trim_blocks=True, lstrip_blocks=True)
    def html_bytes(html):
        return ('\n'.join(line.rstrip() for line in html.splitlines()) + '\n').encode()
    context = catalog.common()
    context['url_for'] = static_url
    atomic_write(directory / 'index.html', html_bytes(env.get_template('index.html').render(**context)))
    template = env.get_template('detail.html')
    for path in catalog.paths():
        context = catalog.page(path)
        context['url_for'] = static_url
        atomic_write(directory / path.strip('/') / 'index.html', html_bytes(template.render(**context)))
    atomic_write(directory / 'sitemap.xml',sitemap_bytes(catalog,catalog.discovery_state))
    atomic_write(directory / 'robots.txt',robots_text().encode())
    return sum(indexable(catalog,path) for path in ['/']+catalog.paths())
