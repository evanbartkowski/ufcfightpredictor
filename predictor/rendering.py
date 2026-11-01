"""Render the same public content for Flask requests and Firebase publications."""
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .catalog import Catalog, ORIGIN
from .publication import atomic_write

ROOT = Path(__file__).resolve().parents[1]


def static_url(endpoint, filename, **query):
    if endpoint != 'static':
        raise ValueError('Only static assets belong in the Hosting export')
    return '/' + filename + ('?' + urlencode(query) if query else '')


def export_pages(service, directory=None, dashboard=None):
    directory = Path(directory or ROOT / 'static')
    catalog = Catalog(service, dashboard)
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
    namespace = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    ET.register_namespace('', namespace)
    root = ET.Element('{' + namespace + '}urlset')
    for path in ['/'] + catalog.paths():
        node = ET.SubElement(root, '{' + namespace + '}url')
        ET.SubElement(node, '{' + namespace + '}loc').text = ORIGIN + path
        if catalog.dashboard['status'].get('last_sync'):
            ET.SubElement(node, '{' + namespace + '}lastmod').text = catalog.dashboard['status']['last_sync']
    atomic_write(directory / 'sitemap.xml', ET.tostring(root, encoding='utf-8', xml_declaration=True))
    atomic_write(directory / 'robots.txt', ('User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: ' + ORIGIN + '/sitemap.xml\n').encode())
    return len(catalog.paths()) + 1
