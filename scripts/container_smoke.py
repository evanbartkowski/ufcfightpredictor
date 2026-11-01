"""Network-free container readiness, imports, persistence, and prediction smoke checks."""
import argparse
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def main():
    parser=argparse.ArgumentParser();parser.add_argument('url');parser.add_argument('--expect-unready',action='store_true');args=parser.parse_args()
    def request(path, payload=None):
        req=Request(args.url+path,data=json.dumps(payload).encode() if payload else None,
                    headers={'Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=10) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)
    for _ in range(45):
        try:
            status,health=request('/health')
            if status==200:break
        except (URLError,TimeoutError):
            time.sleep(1)
    else:raise AssertionError('Container liveness did not become available')
    status,ready=request('/ready')
    if args.expect_unready:
        assert status==503 and not ready['model_ready']
        assert request('/api/matchup',dict(red='Max Holloway',blue='Conor McGregor'))[0]==503
    else:
        assert status==200 and ready['model_ready']
        code,a=request('/api/matchup',dict(red='Max Holloway',blue='Conor McGregor'))
        code_b,b=request('/api/matchup',dict(red='Conor McGregor',blue='Max Holloway'))
        assert code==code_b==200
        assert abs(a['red_probability']-b['blue_probability'])<1e-7
        assert len(a['features'])==37
        assert request('/api/fighters?q=Holloway&per_page=2')[1]['total']>=1
        assert request('/api/overview')[0]==200
    print('Clean container passed:', 'liveness 200/readiness 503 without a model' if args.expect_unready else 'prebuilt model, readiness, normalized predictions, corner symmetry, paginated API')


if __name__=='__main__':main()
