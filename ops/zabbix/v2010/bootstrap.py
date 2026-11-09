"""Reconcile only the V2-010 owned objects; never delete existing configuration."""
from __future__ import annotations
import json
from pathlib import Path
import secrets
import urllib.request

ROOT=Path('/opt/cosmotech/zabbix')
PREFIX='CosmoTech V2-010'
LAB='cosmotech-v2010-isolated-lab'
TAGS=[{'tag':'cosmotech_scope','value':'v2-010'},{'tag':'service','value':'v2010-isolated-lab'}]


class API:
    def __init__(self,url='http://127.0.0.1:8088/api_jsonrpc.php',credential=None):
        self.url=url;self.token=None
        credential=credential or json.loads((ROOT/'secrets/credentials.json').read_text())
        self.token=self.call('user.login',{'username':credential['username'],'password':credential['password']})
    def call(self,method,params):
        if method.endswith('.delete'):raise ValueError('Deletion is outside bootstrap scope')
        headers={'Content-Type':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        request=urllib.request.Request(self.url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers=headers)
        with urllib.request.urlopen(request,timeout=30) as response:d=json.load(response)
        if 'error' in d:
            # Only schema diagnostics: never echo values, tokens, macro contents or request bodies.
            error=d['error'];detail=str(error.get('data',''))
            import re
            safe=re.search(r'(?:Invalid|Unexpected|Missing|No permissions).*?(?:parameter|object|method)[^:]*',detail)
            raise RuntimeError(method+' failed ('+str(error.get('code'))+'); '+(safe[0][:120] if safe else 'details withheld'))
        return d['result']
    def upsert(self,kind,idfield,filter,payload,**get):
        rows=self.call(kind+'.get',{'filter':filter,'output':[idfield],**get})
        if len(rows)>1:raise ValueError('Ambiguous owned resource')
        if rows:
            self.call(kind+'.update',{idfield:rows[0][idfield],**payload});return rows[0][idfield]
        return self.call(kind+'.create',payload)[idfield+'s'][0]


def credentials():
    file=ROOT/'secrets/notifications.json'
    if file.exists():return json.loads(file.read_text())
    value={'username':'laboratorio','password':secrets.token_urlsafe(32),'ingest_token':secrets.token_urlsafe(48),'bot_password':secrets.token_urlsafe(40)}
    file.write_text(json.dumps(value));file.chmod(0o444)
    return value


def item(api,host,key,name,**kw):
    return api.upsert('item','itemid',{'key_':key},{'hostid':host,'key_':key,'name':name,'type':2,'value_type':3,'delay':'0','history':'7d',**kw},hostids=[host])


def trigger(api,host,name,expression,tags):
    return api.upsert('trigger','triggerid',{'description':name},{'description':name,'expression':expression,'priority':3,'tags':tags},hostids=[host])


def reconcile(api,secret):
    group=api.upsert('hostgroup','groupid',{'name':PREFIX},{'name':PREFIX})
    hosts=api.call('host.get',{'filter':{'host':LAB},'output':['hostid']})
    if hosts:host=hosts[0]['hostid']
    else:host=api.call('host.create',{'host':LAB,'name':PREFIX+' isolated faults','groups':[{'groupid':group}],'tags':TAGS})['hostids'][0]
    widgets={}
    expressions={'http_errors':'>0','latency_ms':'>500','service_available':'=0','agent_available':'=0'}
    for key,suffix in expressions.items():
        ident=item(api,host,'v2010.lab.'+key,PREFIX+' '+key)
        widgets[key]=ident
    discovery=api.upsert('discoveryrule','itemid',{'key_':'v2010.lab.discovery'},
        {'hostid':host,'name':PREFIX+' scenario discovery','key_':'v2010.lab.discovery','type':2,'delay':'0'},hostids=[host])
    api.upsert('itemprototype','itemid',{'key_':'v2010.lab.state[{#SCENARIO}]'},
        {'hostid':host,'ruleid':discovery,'name':PREFIX+' {#SCENARIO} fault state','key_':'v2010.lab.state[{#SCENARIO}]','type':2,'value_type':3,'delay':'0','history':'7d'},discoveryids=[discovery])
    api.upsert('triggerprototype','triggerid',{'description':PREFIX+' isolated {#SCENARIO}'},
        {'description':PREFIX+' isolated {#SCENARIO}','expression':'last(/'+LAB+'/v2010.lab.state[{#SCENARIO}])=1','priority':3,'tags':TAGS+[{'tag':'scenario','value':'{#SCENARIO}'}]},discoveryids=[discovery])
    # Native telemetry query (24), JSON values (6), no embedded ClickHouse credentials.
    for service in ['orders-api','orders-worker']:
        rows=api.call('host.get',{'filter':{'host':'cosmotech-apm-'+service},'output':['hostid'],'selectHostGroups':['groupid']})
        if not rows:raise ValueError('Existing APM host is required; do not fabricate telemetry')
        apm=rows[0];groups=[{'groupid':g['groupid']} for g in apm['hostgroups']]
        if group not in [g['groupid'] for g in groups]:api.call('host.update',{'hostid':apm['hostid'],'groups':groups+[{'groupid':group}]})
        query={'signal_type':2,'columns':[],'aggregated_columns':[{'function':4,'alias':'error_logs'}],
            'filter':{'evaltype':1,'conditions':[{'column':'ServiceName','operator':0,'value':service},{'column':'SeverityNumber','operator':5,'value':'17'}]}}
        master=item(api,apm['hostid'],'v2010.apm.error_logs.query',PREFIX+' native '+service+' error logs',type=24,value_type=6,delay='60s',timeout='10s',lookback_limit='5m',granularity='60s',query=query)
        script="var d=JSON.parse(value);if(!Array.isArray(d))throw 'Invalid telemetry result';return d.reduce(function(n,r){if(r.error_logs===undefined)throw 'Missing count';return n+Number(r.error_logs)},0);"
        count=item(api,apm['hostid'],'v2010.apm.error_logs.count',PREFIX+' '+service+' error logs in 5m',type=18,master_itemid=master,preprocessing=[{'type':21,'params':script}])
        tags=[{'tag':'cosmotech_scope','value':'v2-010'},{'tag':'service','value':service}]
        trigger(api,apm['hostid'],PREFIX+' '+service+' error logs','last(/cosmotech-apm-'+service+'/v2010.apm.error_logs.count)>0',tags)
        widgets[service+' error logs']=count
    # CEP only processes newly-owned events, never discards/closes existing incidents.
    cepfilter={'evaltype':0,'conditions':[{'type':26,'operator':0,'tag':'cosmotech_scope','tag_value':'v2-010'}]}
    api.upsert('ceprule','cep_ruleid',{'name':PREFIX+' enrichment'},{'name':PREFIX+' enrichment','window_type':0,'sortorder':100,'status':0,'filter':cepfilter,'operations':[{'sortorder':0,'execute_when':0,'type':11,'tag':'managed_by','tag_value':'cosmotech-v2010'}]})
    api.upsert('ceprule','cep_ruleid',{'name':PREFIX+' service cause and symptoms'},{'name':PREFIX+' service cause and symptoms','window_type':2,'sortorder':110,'status':0,'filter':cepfilter,'window':{'duration':'90s','group_by_tags':1,'tags':['service'],'event_count_tag':'symptoms'},'operations':[]})
    js="var p=JSON.parse(value), tags=JSON.parse(p.tags), service='v2010-isolated-lab';for(var i=0;i<tags.length;i++){if(tags[i].tag==='service')service=tags[i].value;}var r=new HttpRequest();r.addHeader('Content-Type: application/json');r.addHeader('Authorization: Bearer '+p.token);r.post(p.url,JSON.stringify({event_id:String(p.event_id),state:p.event_value==='0'?'recovery':'problem',service:service,severity:Number(p.severity),occurred_at:Math.floor(Date.now()/1000)}));if(r.getStatus()!==202)throw 'Webhook delivery failed';return 'OK';"
    macro='{$COSMOTECH.V2010.WEBHOOK.TOKEN}'
    for h in [host]+[r['hostid'] for r in api.call('host.get',{'filter':{'host':['cosmotech-apm-orders-api','cosmotech-apm-orders-worker']},'output':['hostid']})]:
        api.upsert('usermacro','hostmacroid',{'macro':macro},{'hostid':h,'macro':macro,'value':secret['ingest_token'],'type':1},hostids=[h])
    media=api.upsert('mediatype','mediatypeid',{'name':PREFIX+' notification webhook'},{'name':PREFIX+' notification webhook','type':4,'script':js,'parameters':[{'name':k,'value':v} for k,v in {'url':'http://notifications:8094/events','token':macro,'event_id':'{EVENT.ID}','event_value':'{EVENT.VALUE}','tags':'{EVENT.TAGSJSON}','severity':'{EVENT.NSEVERITY}'}.items()]})
    users=api.call('user.get',{'filter':{'username':'cosmotech-v2010-notifier'},'output':['userid']})
    usergroup=api.upsert('usergroup','usrgrpid',{'name':PREFIX+' notification service'},{'name':PREFIX+' notification service','gui_access':3,'hostgroup_rights':[{'id':group,'permission':2}]})
    media_entry={'mediatypeid':media,'sendto':['lab'],'active':0,'severity':63,'period':'1-7,00:00-24:00'}
    if users:
        user=users[0]['userid'];api.call('user.update',{'userid':user,'medias':[media_entry]})
    else:
        roles=api.call('role.get',{'output':['roleid','type']});role=next(r['roleid'] for r in roles if str(r['type'])=='1')
        user=api.call('user.create',{'username':'cosmotech-v2010-notifier','passwd':secret['bot_password'],'roleid':role,'usrgrps':[{'usrgrpid':usergroup}],'medias':[media_entry]})['userids'][0]
    operation={'operationtype':0,'opmessage_usr':[{'userid':user}],'opmessage':{'default_msg':0,'subject':PREFIX,'message':'State-only laboratory event','mediatypeid':media}}
    api.upsert('action','actionid',{'name':PREFIX+' notifications'},{'name':PREFIX+' notifications','eventsource':0,'status':0,'filter':{'evaltype':0,'conditions':[{'conditiontype':26,'operator':0,'value':'v2-010','value2':'cosmotech_scope'}]},'operations':[operation],'recovery_operations':[operation]})
    entries=[]
    for i,(name,ident) in enumerate(widgets.items()):entries.append({'type':'item','name':name,'x':(i%3)*24,'y':(i//3)*4,'width':24,'height':4,'fields':[{'type':4,'name':'itemid.0','value':ident}]})
    dashboard=api.upsert('dashboard','dashboardid',{'name':PREFIX+' observability and isolated faults'},{'name':PREFIX+' observability and isolated faults','pages':[{'name':PREFIX,'widgets':entries}]})
    return {'lab_host':LAB,'native_telemetry_queries':2,'new_error_log_triggers':2,'isolated_fault_trigger_prototypes':1,'native_discovery_rules':1,'cep_rules':2,'dashboard_id':dashboard,'webhook_media':media,'notification_action_scoped':True}


if __name__=='__main__':
    secret=credentials();api=API()
    try:print(json.dumps(reconcile(api,secret)))
    finally:api.call('user.logout',[])
