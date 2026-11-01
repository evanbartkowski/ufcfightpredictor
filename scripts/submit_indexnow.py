"""Notify IndexNow of changed public pages after verifying the live key file."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
import requests

ROOT=Path(__file__).resolve().parents[1]


def changed_paths(current, previous):
    return sorted({path for path,item in current.items() if previous.get(path)!=item['hash']} | (set(previous)-set(current)))


def submit(origin, state_file, session=None):
    session=session or requests.Session()
    config=json.loads((ROOT/'static/indexnow.json').read_text())
    if origin.rstrip('/')!=config['origin']: raise ValueError('Submission origin differs from the verified site')
    key=config['key']; key_url=origin+'/'+key+'.txt'
    response=session.get(key_url,timeout=(10,30));response.raise_for_status()
    if response.text.strip()!=key: raise ValueError('The live IndexNow key file does not match')
    response=session.get(origin+'/api/discovery.json',timeout=(10,45));response.raise_for_status()
    current=response.json()['pages']
    previous=json.loads(state_file.read_text()) if state_file.exists() else {}
    paths=changed_paths(current,previous)
    if any(not p.startswith('/') or p.startswith('//') or '?' in p or '#' in p for p in paths): raise ValueError('Invalid canonical URL path')
    for offset in range(0,len(paths),10000):
        batch=paths[offset:offset+10000]
        response=session.post('https://api.indexnow.org/indexnow',json=dict(
            host=urlparse(origin).hostname,key=key,keyLocation=key_url,urlList=[origin+p for p in batch]),timeout=(15,60))
        if response.status_code not in (200,202):
            raise RuntimeError(f'IndexNow returned HTTP {response.status_code}; submission state was retained for retry')
    state_file.parent.mkdir(parents=True,exist_ok=True)
    temporary=state_file.with_suffix('.tmp');temporary.write_text(json.dumps({p:v['hash'] for p,v in current.items()},sort_keys=True))
    temporary.replace(state_file)
    return dict(status='accepted' if paths else 'unchanged',submitted_urls=len(paths),origin=origin,
                note='Submission acknowledges receipt; crawling and indexing are controlled by the search engines.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='https://mma-oracle.web.app');parser.add_argument('--state-file',type=Path,default=ROOT/'data/indexnow-state.json');args=parser.parse_args()
    print(json.dumps(submit(args.url.rstrip('/'),args.state_file),indent=2))
