"""Small cached views and deterministic pages for Flask and static Hosting."""
import json

from .store import identity
from .links import fighter_path

PAGE_SIZE = 48
FIGHT_KEYS = ('id','date','event','red','blue','pick','confidence','red_probability','blue_probability',
              'actual_winner','correct','method','path','event_path','model_version','issued_at','card_section','section_source')
INDEX_KEYS = ('name','path','record','weight_class')


def page(items, number=1, per_page=PAGE_SIZE):
    return dict(items=items[(number-1)*per_page:number*per_page], total=len(items), page=number,
                per_page=per_page, pages=max(1,(len(items)+per_page-1)//per_page))


def overview(dashboard, mode='server'):
    result = {key:value for key,value in dashboard.items() if key not in ('fighters','results','images','model')}
    result['api_mode'] = mode
    result['fighters'] = [dict({key:f[key] for key in INDEX_KEYS if f.get(key) is not None}, profile_page=index//PAGE_SIZE+1)
                          for index,f in enumerate(dashboard['fighters'])]
    detail_only = {'features','feature_names','red_history','blue_history','red_url','blue_url','event_url','input_provenance'}
    result['upcoming'] = [{key:value for key,value in f.items() if key not in detail_only} for f in dashboard['upcoming']]
    result['results_total'] = len(dashboard['results'])
    result['results'] = [{key:f.get(key) for key in FIGHT_KEYS} for f in dashboard['results'][:PAGE_SIZE]]
    if dashboard.get('model'):
        model = dict(dashboard['model'])
        for key in ('candidate_metrics','calibration','confidence_distribution','events_history','weight_class_stats','feature_importances'):
            model.pop(key,None)
        model['metrics'] = {key:value for key,value in model['metrics'].items() if key not in ('events_history','holdout_bouts')}
        result['model'] = model
    else:
        result['model'] = None
    return result


def prediction_page(dashboard, kind, number=1, per_page=PAGE_SIZE):
    rows = dashboard[kind]
    if kind == 'results':
        rows = [{key:f.get(key) for key in FIGHT_KEYS} for f in rows]
    return page(rows,number,per_page)


def analytics_page(bundle, kind, number=1, per_page=100):
    metric = {'events':'events_history','holdout':'holdout_bouts'}[kind]
    return page(bundle.get('metrics', {}).get(metric, []),number,per_page)


def export_api(directory, dashboard, bundle, write):
    directory.mkdir(parents=True,exist_ok=True)
    def emit(path,value):
        write(directory/path,json.dumps(value,separators=(',',':')).encode())
    emit('overview.json',overview(dashboard,mode='static'))
    emit('fighter-images.json',dashboard.get('images', {}))
    for kind in ('upcoming','results'):
        for number in range(1,max(1,(len(dashboard[kind])+PAGE_SIZE-1)//PAGE_SIZE)+1):
            emit(f'predictions/{kind}/page/{number}.json',prediction_page(dashboard,kind,number))
    fighters = dashboard['fighters']
    for number in range(1,max(1,(len(fighters)+PAGE_SIZE-1)//PAGE_SIZE)+1):
        emit(f'fighters/page/{number}.json',page(fighters,number))
    for fighter in fighters:
        emit('fighters/'+identity(fighter['name'])+'.json',dict(fighter,path=fighter.get('path') or fighter_path(fighter['name'])))
    for kind in ('events','holdout'):
        first = analytics_page(bundle or {},kind)
        for number in range(1,first['pages']+1):
            emit(f'analytics/{kind}/page/{number}.json',analytics_page(bundle or {},kind,number))
