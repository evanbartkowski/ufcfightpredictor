"""Search metadata and truthful, persistent page modification timestamps."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ORIGIN = 'https://mma-oracle.web.app'
REPOSITORY = 'https://github.com/evanbartkowski/ufcfightpredictor'
HOME_TITLE = 'MMA Cipher (MMACipher) | UFC Predictions & Fighter Stats'
HOME_DESCRIPTION = 'MMA Cipher (MMACipher): UFC fight predictions, fighter stats, upcoming event cards, and a transparent main-card prediction record with model evidence.'
VOLATILE = {'revision','issued_at','fetched_at','checked_at','published_at','trained_at','schedule_fetched_at','input_provenance','odds_status','status','last_sync','profile_errors'}
TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'


def stable(value):
    if isinstance(value,dict): return {k:stable(v) for k,v in value.items() if k not in VOLATILE}
    if isinstance(value,list): return [stable(v) for v in value]
    return value


def indexable(catalog, path):
    if path == '/status/': return False
    if path in catalog.fighters:
        profile = catalog.fighters[path]
        from .store import identity
        name = identity(profile['name'])
        return bool(catalog.bouts[name] or profile.get('record') or any(name in (identity(f['red']),identity(f['blue'])) for f in catalog.dashboard['upcoming']))
    return True


def signature(catalog, path, layout):
    if path in catalog.fighters:
        from .store import identity
        profile = catalog.fighters[path]
        value = [profile,catalog.bouts[identity(profile['name'])],catalog.fighter(path)['upcoming']]
    elif path in catalog.events: value = catalog.events[path]
    elif path in catalog.forecasts: value = catalog.forecasts[path]
    elif path == '/': value = {k:catalog.dashboard.get(k) for k in ('upcoming','live_accuracy','scored_predictions','model','total_fights')}
    elif path == '/fighters/': value = list(catalog.fighters.values())
    elif path == '/events/': value = catalog.event_list
    elif path == '/fights/': value = list(catalog.forecasts.values())
    elif path in ('/model/','/about/'): value = catalog.dashboard.get('model')
    else: value = catalog.paths()
    return hashlib.sha256((layout+json.dumps(stable(value),sort_keys=True,separators=(',',':'))).encode()).hexdigest()


def update_state(catalog, previous, at):
    # Primary templates and metadata can change meaningful page content even
    # when the fight data did not. CSS-only changes do not reset these dates.
    names = ('index.html','detail.html','_public.html','_directories.html','_seo.html','_discovery_pages.html')
    source = b''.join((TEMPLATES/name).read_bytes() for name in names if (TEMPLATES/name).exists()) + Path(__file__).read_bytes()
    layout = hashlib.sha256(source.replace(b'\r\n',b'\n')).hexdigest()
    old = previous.get('pages',{})
    pages = {}
    for path in ['/'] + catalog.paths():
        if not indexable(catalog,path): continue
        digest = signature(catalog,path,layout)
        prior = old.get(path,{})
        pages[path] = dict(hash=digest,lastmod=prior['lastmod'] if prior.get('hash')==digest and prior.get('lastmod') else at)
    return dict(schema=1,pages=pages)


def robots_text():
    # Crawlers need these JSON resources to render the homepage successfully.
    # API responses carry X-Robots-Tag: noindex; bulk state remains blocked.
    return ('User-agent: *\nAllow: /api/overview\nAllow: /api/update\nAllow: /api/dashboard\n'
            'Allow: /api/fighter-images.json\nDisallow: /api/\nAllow: /\nSitemap: '+ORIGIN+'/sitemap.xml\n')


def sitemap_bytes(catalog, state):
    namespace='http://www.sitemaps.org/schemas/sitemap/0.9'; ET.register_namespace('',namespace)
    root=ET.Element('{'+namespace+'}urlset')
    for path in ['/'] + catalog.paths():
        if not indexable(catalog,path): continue
        node=ET.SubElement(root,'{'+namespace+'}url')
        ET.SubElement(node,'{'+namespace+'}loc').text=ORIGIN+path
        if state.get('pages',{}).get(path,{}).get('lastmod'):
            ET.SubElement(node,'{'+namespace+'}lastmod').text=state['pages'][path]['lastmod']
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def metadata(catalog, path, context, state):
    canonical=ORIGIN+path
    title=HOME_TITLE if path=='/' else context['title']+' | MMA Cipher'
    description=HOME_DESCRIPTION if path=='/' else context['description']
    kind=context.get('kind','home')
    organization={'@type':'Organization','@id':ORIGIN+'/#organization','name':'MMA Cipher','alternateName':'MMACipher','url':ORIGIN+'/',
        'sameAs':[REPOSITORY],'logo':{'@type':'ImageObject','url':ORIGIN+'/icons/icon-512.png','width':512,'height':512}}
    site={'@type':'WebSite','@id':ORIGIN+'/#website','url':ORIGIN+'/','name':'MMA Cipher','alternateName':['MMACipher','MMA Cipher Predictions'],
          'inLanguage':'en','publisher':{'@id':ORIGIN+'/#organization'}}
    page={'@type':'ProfilePage' if kind=='fighter' else 'CollectionPage' if kind in ('fighters','events','fights','sitemap') else 'AboutPage' if kind=='about' else 'WebPage',
          '@id':canonical+'#webpage','url':canonical,'name':title,'description':description,'inLanguage':'en','isPartOf':{'@id':ORIGIN+'/#website'},
          'primaryImageOfPage':{'@type':'ImageObject','url':ORIGIN+'/social-preview.png','width':1200,'height':630}}
    changed=state.get('pages',{}).get(path,{}).get('lastmod')
    if changed: page['dateModified']=changed
    graph=[organization,site,page]
    if kind=='fighter':
        profile=context['profile']; person={'@type':'Person','@id':canonical+'#fighter','name':profile['name'],'url':canonical,'description':description}
        image=context['portrait'](profile['name'])
        if image: person['image']=image
        page['mainEntity']={'@id':person['@id']}; graph.append(person)
    elif kind=='event':
        event=context['event']; graph.append({'@type':'SportsEvent','@id':canonical+'#event','name':event['name'],'startDate':event['date'],
            'url':canonical,'sport':'Mixed martial arts','competitor':[{'@type':'Person','name':name,'url':ORIGIN+catalog.fighter_url(name)} for name in dict.fromkeys(f[c] for f in event['fights'] for c in ('red','blue'))]})
        page['about']={'@id':canonical+'#event'}
    elif kind=='fight' and context['fight'].get('pick'):
        fight=context['fight']; article={'@type':'Article','@id':canonical+'#forecast','headline':context['title'],'description':description,
            'mainEntityOfPage':{'@id':page['@id']},'author':{'@id':organization['@id']},'publisher':{'@id':organization['@id']},
            'image':ORIGIN+'/social-preview.png','isAccessibleForFree':True}
        if fight.get('issued_at'): article['datePublished']=fight['issued_at']
        if changed: article['dateModified']=changed
        page['mainEntity']={'@id':article['@id']};graph.append(article)
    if path!='/':
        parent='/fighters/' if kind=='fighter' else '/events/' if kind=='event' else '/fights/' if kind=='fight' else None
        entries=[('/', 'MMA Cipher')]
        if parent: entries.append((parent,{'/fighters/':'Fighter profiles','/events/':'Event cards','/fights/':'Issued forecasts'}[parent]))
        entries.append((path,context['title']))
        crumb={'@type':'BreadcrumbList','@id':canonical+'#breadcrumb','itemListElement':[{'@type':'ListItem','position':i,'name':name,'item':ORIGIN+url} for i,(url,name) in enumerate(entries,1)]}
        graph.append(crumb);page['breadcrumb']={'@id':crumb['@id']}
    return dict(title=title,description=description,canonical=canonical,indexable=indexable(catalog,path),modified=changed,
                graph={'@context':'https://schema.org','@graph':graph},image=ORIGIN+'/social-preview.png',og_type='article' if kind=='fight' and context['fight'].get('pick') else 'website')
