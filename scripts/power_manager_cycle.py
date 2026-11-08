"""Owner-authorized controlled test through the same API as both portal buttons.

Runs under GitHub OIDC, never root or a forged Sites visitor. Every operation
uses normal certification, snapshot, confirmation, lock and recovery guards.
"""
import argparse
import json
import os
import time
import uuid
import urllib.error
import urllib.request
import boto3
from botocore.config import Config

CONFIG=Config(connect_timeout=5,read_timeout=40,retries={'mode':'standard','total_max_attempts':2})


class Controller:
    def __init__(self, run_id):
        self.run_id=run_id
        session=boto3.Session(region_name='us-east-1')
        identity=session.client('sts',config=CONFIG).get_caller_identity()['Arn']
        if not identity.startswith('arn:aws:sts::414053644145:assumed-role/cosmotech-power-deploy/'):
            raise ValueError('Controlled cycle requires the non-root GitHub OIDC publisher role')
        self.client=session.client('lambda',config=CONFIG)
        self.ssm=session.client('ssm',config=Config(connect_timeout=5,read_timeout=20,retries={'mode':'adaptive','total_max_attempts':5}))
        print(json.dumps({'oidc_identity':identity,'run_id':run_id}),flush=True)

    def request(self,method,path,payload=None,*,expected_status=None):
        event={'source':'cosmotech.power.iam-admin','authorization':'OWNER_AUTHORIZED_CONTROLLED_CYCLE',
            'run_id':self.run_id,'method':method,'path':path,'payload':payload or {}}
        response=self.client.invoke(FunctionName='cosmotech-power-api',InvocationType='RequestResponse',Payload=json.dumps(event).encode())
        with response['Payload'] as stream:result=json.load(stream)
        if response.get('FunctionError'):raise RuntimeError('Administrative API invocation failed')
        body=json.loads(result['body'])
        if expected_status is not None:
            if result['statusCode']!=expected_status:raise RuntimeError('Unexpected guard response: '+str(result['statusCode']))
            return body
        if result['statusCode']>=400:raise RuntimeError(json.dumps({'http':result['statusCode'],'error':body},ensure_ascii=False))
        return body

    def state(self):return self.request('GET','/api/status')

    def no_external_commands(self):
        # Unknown active automation can race with a snapshot or restart workloads.
        active=[]
        # Filter on the service, avoiding a scan of the 30-day command history.
        for status in ('Pending','InProgress','Cancelling'):
            for page in self.ssm.get_paginator('list_commands').paginate(Filters=[{'key':'Status','value':status}]):
                for command in page.get('Commands',[]):
                    active.append({'document':command['DocumentName'],'command_id':command['CommandId']})
        if active:raise RuntimeError('Active SSM automation must finish before DOWN: '+json.dumps(active))

    def plan(self,direction):
        value=self.request('POST','/api/plan',{'direction':direction})
        deadline=time.monotonic()+540
        while value.get('pending') and time.monotonic()<deadline:
            time.sleep(5);value=self.request('GET','/api/plans/'+value['id'])
        print(json.dumps({'direction':direction,'plan_id':value['id'],'ready':value.get('ready'),'blockers':value.get('blockers',[]),'targets':value.get('targets',[]),'kubernetes_summary':value.get('kubernetes_summary')},ensure_ascii=False),flush=True)
        if value.get('pending') or not value.get('ready'):raise RuntimeError('Safety/recovery plan rejected; no new power operation submitted')
        return value

    def execute(self,direction):
        plan=self.plan(direction)
        payload={'planId':plan['id'],'confirmation':plan['confirmation'],
            'idempotencyKey':str(uuid.uuid4()),'firstShutdownConsent':True}
        self.request('POST','/api/operations',{**payload,'confirmation':'INVALID_CONFIRMATION'},expected_status=400)
        if direction=='DOWN':
            self.request('POST','/api/operations',{**payload,'firstShutdownConsent':False},expected_status=400)
        op=self.request('POST','/api/operations',payload)
        repeated=self.request('POST','/api/operations',payload)
        if repeated['id']!=op['id']:raise RuntimeError('Idempotency failed')
        self.request('POST','/api/operations',{**payload,'idempotencyKey':str(uuid.uuid4())},expected_status=409)
        print(json.dumps({'live_guards':{'confirmation':True,'down_consent':direction=='DOWN','idempotency':True,'concurrent_operation_rejected':True},'operation_id':op['id']}),flush=True)
        print(json.dumps({'submitted':direction,'operation_id':op['id'],'execution_arn':op.get('execution_arn')}),flush=True)
        deadline=time.monotonic()+3400;previous=None
        while True:
            op=self.request('GET','/api/operations/'+op['id'])
            current=(op['state'],op.get('phase'),op.get('index'))
            if current!=previous:
                print(json.dumps({'direction':direction,'operation_id':op['id'],'state':op['state'],'phase':op.get('phase'),'index':op.get('index'),'error':op.get('error'),'differences':op.get('differences')},ensure_ascii=False),flush=True);previous=current
            if op['state']!='RUNNING':break
            if time.monotonic()>deadline:raise TimeoutError('Operation deadline; inspect control and recover')
            time.sleep(10)
        if op['state']!=('OFF' if direction=='DOWN' else 'ON'):raise RuntimeError('Power operation failed; recovery snapshot remains available')
        return op


def portal_alive():
    url='https://cosmotech-lab-power-manager.botanicalrook.chatgpt.site/'
    try:
        with urllib.request.urlopen(url,timeout=20) as result:status=result.status
    except urllib.error.HTTPError as error:status=error.code
    if status not in (200,401,403):raise RuntimeError('Always-On portal unavailable')
    print(json.dumps({'portal_http':status,'authentication_required':status in (401,403)}),flush=True)


def cycle(controller):
    initial=controller.state()
    if initial['state']['state']!='ON' or not initial.get('execution_enabled'):raise RuntimeError('Initial state/certification does not permit controlled shutdown')
    portal_alive()
    controller.no_external_commands()
    failure=None
    try:
        controller.execute('DOWN')
        if controller.state()['state']['state']!='OFF':raise RuntimeError('OFF state was not confirmed')
        portal_alive()
    except Exception as error:
        failure=error
    finally:
        # Recovery is attempted even when DOWN partially fails. Never clear a
        # live lock or alter snapshots to make this guard pass.
        recovery_deadline=time.monotonic()+3500
        for attempt in range(3):
            state=controller.state()['state']['state']
            while state in ('SHUTTING_DOWN','STARTING','STARTING_UP') and time.monotonic()<recovery_deadline:
                time.sleep(10)
                state=controller.state()['state']['state']
            if state=='ON':break
            if state not in ('OFF','RECOVERY_REQUIRED'):
                time.sleep(20);continue
            try:controller.execute('UP')
            except Exception as error:
                print(json.dumps({'recovery_attempt':attempt+1,'error':str(error)}),flush=True)
                time.sleep(20)
        final=controller.state()
        print(json.dumps({'final_control':final['state'],'final_operation':final.get('operation')},ensure_ascii=False),flush=True)
        if final['state']['state']!='ON':raise RuntimeError('RECOVERY INCIDENT: laboratory recovery remains incomplete')
        portal_alive()
    if failure:raise failure


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--action',choices=['status','cycle','recover'],default='status');args=parser.parse_args()
    controller=Controller(os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1'))
    if args.action=='cycle':cycle(controller)
    elif args.action=='recover':
        state=controller.state()['state']['state']
        if state in ('OFF','RECOVERY_REQUIRED'):controller.execute('UP')
        elif state!='ON':raise RuntimeError('Active operation must finish before recovery')
        if controller.state()['state']['state']!='ON':raise RuntimeError('Recovery did not restore ON')
        portal_alive()
    else:print(json.dumps(controller.state(),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
