"""Concurrent WSGI latency/payload/memory/query measurements; never calls upstream."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc
import os


def resident_memory():
    if os.name=='nt':
        import ctypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(name,ctypes.c_size_t) for name in ('peak','working','a','b','c','d','e','f')]
        info=Counters();info.cb=ctypes.sizeof(info)
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(info),info.cb):
            return round(info.working/1024/1024,3)
    elif Path('/proc/self/status').exists():
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):return round(int(line.split()[1])/1024,3)
    return None

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import create_app


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',default=str(ROOT/'data'));parser.add_argument('--output',default=str(ROOT/'test-results/performance-after.json'));args=parser.parse_args()
    app=create_app(args.data_dir,bootstrap=False,auto_updates=False)
    service=app.extensions['predictor'];store=service.store
    if service.bundle is None:raise SystemExit('Prepare a compatible model before benchmarking')
    store.record_queries=True
    service.dashboard_bytes()
    with app.test_client() as client:client.get('/api/overview')
    results={}
    for path,count,workers,body in [('/api/dashboard',12,1,None),('/api/dashboard',64,8,None),('/api/overview',64,8,None),
        ('/api/fighters?q=Silva&per_page=25',32,8,None),('/api/matchup',24,2,dict(red='Max Holloway',blue='Conor McGregor'))]:
        before=store.diagnostics();builds=service.dashboard_builds;tracemalloc.start()
        def request(index):
            start=time.perf_counter()
            with app.test_client() as client:
                response=client.post(path,json=body,environ_overrides={'REMOTE_ADDR':f'198.51.100.{index+1}'}) if body else client.get(path)
            return (time.perf_counter()-start)*1000,response.status_code,len(response.data)
        with ThreadPoolExecutor(max_workers=workers) as pool:samples=list(pool.map(request,range(count)))
        _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();after=store.diagnostics()
        times=sorted(row[0] for row in samples)
        results[path+f' [{workers} workers]']=dict(requests=count,workers=workers,p50_ms=round(statistics.median(times),3),
            p95_ms=round(times[min(len(times)-1,int(len(times)*.95))],3),
            payload_bytes=samples[0][2],statuses={str(status):sum(r[1]==status for r in samples) for status in set(r[1] for r in samples)},
            peak_memory_mib=round(peak/1024/1024,3),dashboard_rebuilds=service.dashboard_builds-builds,
            resident_memory_mib=resident_memory(),
            connections_per_request=(after.get('connections',0)-before.get('connections',0))/count,
            statements_per_request=(after.get('statements',0)-before.get('statements',0))/count)
    # Compare scoped reuse with short-lived connections for the same reads.
    connections={}
    for scoped in (False,True):
        before=store.diagnostics();start=time.perf_counter()
        for _ in range(50):
            if scoped:
                with store.scope():
                    for key in ('last_sync','odds_checked_at','seeded'):store.meta(key)
            else:
                for key in ('last_sync','odds_checked_at','seeded'):store.meta(key)
        connections['request_scope' if scoped else 'individual_operations']=dict(elapsed_ms=(time.perf_counter()-start)*1000,connections=store.diagnostics()['connections']-before['connections'])
    report=dict(benchmark='In-process concurrent Flask/WSGI requests with tracemalloc enabled; no external HTTP',endpoints=results,connection_reuse=connections)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    assert all(all(int(status)<400 for status in row['statuses']) for row in results.values()),'Unexpected API errors during load'


if __name__=='__main__':main()
