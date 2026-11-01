"""Keep 24 automated snapshots without touching manual releases or live content."""
import os

import requests

BASE = 'https://firebasehosting.googleapis.com/v1beta1'
SITE = 'projects/mma-oracle/sites/mma-oracle'
MARKER = '[mma-cipher-auto]'


def retired_versions(releases, active, keep=24):
    # A manually released/rolled-back version is protected even if its original
    # release was made by the updater. Only our marked, finalized snapshots count.
    protected = {active} | {r.get('version', {}).get('name') for r in releases
                           if not r.get('message', '').startswith(MARKER)}
    automated = sorted((r for r in releases if r.get('message', '').startswith(MARKER)
                        and r.get('version', {}).get('status') == 'FINALIZED'),
                       key=lambda r: r.get('releaseTime', ''), reverse=True)
    protected.update(r['version']['name'] for r in automated[:keep])
    return sorted({r['version']['name'] for r in automated[keep:]} - protected)


def main():
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + os.environ['GOOGLE_ACCESS_TOKEN']
    channel_url = f'{BASE}/{SITE}/channels/live'
    response = session.get(channel_url, timeout=30)
    response.raise_for_status()
    active = response.json()['release']['version']['name']
    response = session.get(channel_url + '/releases', params={'pageSize': 100}, timeout=30)
    response.raise_for_status()
    retired = retired_versions(response.json().get('releases', []), active)
    for version in retired:
        if not version.startswith(SITE + '/versions/'):
            raise ValueError('Refusing to delete a version outside this Hosting site')
        response = session.delete(f'{BASE}/{version}', timeout=30)
        response.raise_for_status()
    print(f'Retired {len(retired)} superseded automated snapshots; retained the latest 24.')


if __name__ == '__main__':
    main()
