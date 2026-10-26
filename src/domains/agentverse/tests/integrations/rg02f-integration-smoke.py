"""Dependency-light RG-02F client ABI and exposure smoke checks.
Run: python rg02f-integration-smoke.py --module /path/to/manifest_projection.py
Not a replacement for the CAOS API or browser integration suite.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import sys

parser=argparse.ArgumentParser()
parser.add_argument('--module', default='/mnt/data/rg02f-work/src/domains/agentverse/security/manifest_projection.py')
args=parser.parse_args()
spec=importlib.util.spec_from_file_location('caos_rg02f_projection_smoke', args.module)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)

def project(raw):
    return module.project_metadata('tool',raw,lambda *_:True,lambda *_:True,lambda *_:True)

raw={'key':'web_crawler','ownership':'global','ui':{'components':[
    {'id':'find','widget':'TextField','type':'text','props':{'label':'Find'}},
    {'id':'extensions','widget':'SelectField','type':'select','props':{'options':[{'value':'all','label':'All'}]}},
    {'id':'secret','widget':'HiddenField','type':'hidden','props':{'label':'SENTINEL'}},
], 'config':{
    'feature_panels':[{'id':'search','label':'Search','fields':['find','secret'],'groups':[{'id':'visible','fields':['extensions','secret']},{'id':'hidden','fields':['secret']}]},{'id':'private','fields':['secret']}],
    'search_behavior':{'field':'find','inference':[{'match':'option_value','source_options':'extensions','target_field':'find','target_mode':'append'},{'match':'option_value','source_options':'secret','target_field':'find'}]},
    'operation_binding':{'source':'operation','kind':'crawl','actions':{'cancel':{'type':'http_request','method':'POST','endpoint':'/v1/operations/{run_id}/cancel','path_params':{'run_id':{'from_state':'operation.run_id'}}}}},
}}}
checks=[]
def verify(label,ok):
    assert ok,label
    checks.append(label)

p=project(raw)
ui=p['ui'];cfg=ui['config']
verify('public form controls preserved', [x['id'] for x in ui['components']]==['find','extensions'])
verify('hidden component not serialized', 'SENTINEL' not in json.dumps(p))
verify('visible panel remains', [x['id'] for x in cfg['feature_panels']]==['search'])
verify('hidden panel fields removed',cfg['feature_panels'][0]['fields']==['find'] and cfg['feature_panels'][0]['groups'][0]['fields']==['extensions'])
verify('hidden-only group removed',len(cfg['feature_panels'][0]['groups'])==1)
verify('search inference filtered',len(cfg['search_behavior']['inference'])==1 and cfg['search_behavior']['inference'][0]['target_field']=='find')
verify('approved operation cancel retained',cfg['operation_binding']['actions']['cancel']['endpoint']=='/v1/operations/{run_id}/cancel')
raw['ui']['config']['operation_binding']['actions']['cancel']['endpoint']='https://example.org/steal'
verify('arbitrary cancel endpoint rejected',not project(raw)['ui']['config']['operation_binding'].get('actions'))
raw['ui']['config']['operation_binding']['actions']['cancel'].update(endpoint='/v1/operations/{run_id}/cancel',method='DELETE')
verify('unreviewed cancellation method rejected',not project(raw)['ui']['config']['operation_binding'].get('actions'))
print(f'PASS: {len(checks)} isolated RG-02F client projection smoke checks')
for x in checks:print('  - '+x)
