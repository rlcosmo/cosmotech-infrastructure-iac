"""Exercise real checkpoint/dispatch code against an in-memory AWS state model."""
import copy
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from power_manager import executor, api


class ConditionalFailed(Exception):pass
class MissingInvocation(Exception):pass


def condition_ok(expression,item):
    if expression is None:return True
    e=expression.get_expression();op=e['operator'];values=e['values']
    if op=='attribute_not_exists':return values[0].name not in item
    if op=='=':return item.get(values[0].name)==values[1]
    if op=='OR':return any(condition_ok(v,item) for v in values)
    raise AssertionError(op)


class Table:
    def __init__(self):
        self.data={};self.meta=SimpleNamespace(client=SimpleNamespace(exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailed)))
    def get_item(self,Key,**kw):return {'Item':copy.deepcopy(self.data[Key['pk']])} if Key['pk'] in self.data else {}
    def put_item(self,Item,ConditionExpression=None):
        if not condition_ok(ConditionExpression,self.data.get(Item['pk'],{})):raise ConditionalFailed()
        self.data[Item['pk']]=copy.deepcopy(Item)
    def delete_item(self,Key,ConditionExpression=None):
        if not condition_ok(ConditionExpression,self.data.get(Key['pk'],{})):raise ConditionalFailed()
        self.data.pop(Key['pk'],None)


class AWS:
    def __init__(self):
        self.table=Table();self.events=[];self.objects={};self.commands={};self.fail_phase=None;self.network=[]
        self.instances={}
        for name in ['control','itsm','zabbix']:
            iid=executor.ec2_id(name);self.instances[iid]={'InstanceId':iid,'State':{'Name':'running'},'BlockDeviceMappings':[]}
        self.workers=['worker-1','worker-2','worker-3'];self.volumes=[{'VolumeId':'data-volume'}]
        for n in self.workers:
            volume={'VolumeId':n+'-root'};self.volumes.append(volume)
            self.instances[n]={'InstanceId':n,'State':{'Name':'running'},'BlockDeviceMappings':[{'DeviceName':'/dev/xvda','Ebs':{**volume,'DeleteOnTermination':True}}]}
        self.capacity={'minSize':1,'maxSize':3,'desiredSize':3}
        self.group={'AutoScalingGroupName':'group','AutoScalingGroupARN':'arn:group','Instances':self.members(),'SuspendedProcesses':[]}
        self.kube={'workloads':[{'namespace':'orders-worker','kind':'deployments','name':'orders-worker','template_hash':'hash','replicas':1}], 'argo':[], 'blockers':[]}
        self.approved={'key':'plans/approved/kubernetes.json','version':'1'}
        self.objects[(self.approved['key'],'1')]=json.dumps(self.kube).encode()
        self.exceptions=SimpleNamespace(InvocationDoesNotExist=MissingInvocation)
    def members(self):return [{'InstanceId':w,'HealthStatus':'Healthy','LifecycleState':'InService'} for w in self.workers]
    def can_paginate(self,operation):return False
    def describe_instances(self,InstanceIds):return {'Reservations':[{'Instances':[copy.deepcopy(self.instances[i]) for i in InstanceIds]}]}
    def describe_instance_attribute(self,**kwargs):return {'DisableApiStop':{'Value':False}}
    def describe_volumes(self):return {'Volumes':copy.deepcopy(self.volumes)}
    def describe_addresses(self):return {'Addresses':self.network}
    def describe_nat_gateways(self):return {'NatGateways':[]}
    def describe_vpcs(self):return {'Vpcs':[]}
    def describe_subnets(self):return {'Subnets':[]}
    def describe_route_tables(self):return {'RouteTables':[]}
    def describe_security_groups(self):return {'SecurityGroups':[]}
    def describe_internet_gateways(self):return {'InternetGateways':[]}
    def describe_load_balancers(self):return {'LoadBalancers':[]}
    def describe_target_groups(self):return {'TargetGroups':[]}
    def list_hosted_zones(self):return {'HostedZones':[]}
    def describe_nodegroup(self,**kw):return {'nodegroup':{'nodegroupName':'nodes','scalingConfig':copy.deepcopy(self.capacity),'status':'ACTIVE','resources':{'autoScalingGroups':[{'name':'group'}]}}}
    def describe_auto_scaling_groups(self,**kw):return {'AutoScalingGroups':[copy.deepcopy(self.group)]}
    def put_object(self,Bucket,Key,Body,**kwargs):
        if kwargs.get('IfNoneMatch')=='*' and any(k==Key for k,v in self.objects):raise AssertionError('Snapshot overwritten')
        version=str(len(self.objects)+1);self.objects[(Key,version)]=Body;self.events.append(('snapshot',Key));return {'VersionId':version}
    def get_object(self,Bucket,Key,VersionId):return {'Body':io.BytesIO(self.objects[(Key,VersionId)])}
    def send_command(self,Parameters,**kw):
        if 'Mode' in Parameters:
            identifier=str(len(self.commands)+1);iid=kw['InstanceIds'][0]
            ref={'key':'plans/'+Parameters['PlanId'][0]+'/hosts/'+iid+'.json','version':'host-'+identifier}
            self.objects[(ref['key'],ref['version'])]=json.dumps({'host':iid,'containers':[],'units':{}}).encode()
            self.commands[identifier]={'Status':'Success','StandardOutputContent':json.dumps({'reference':ref,'safe_restore_validated':True})}
            self.events.append(('host',Parameters['Mode'][0]));return {'Command':{'CommandId':identifier}}
        phase=Parameters['Phase'][0];identifier=str(len(self.commands)+1)
        output={}
        if phase=='SNAPSHOT':
            key='plans/'+Parameters['PlanId'][0]+'/kubernetes.json';version='capture-'+identifier
            self.objects[(key,version)]=json.dumps(self.kube).encode();output={'version':version}
        self.commands[identifier]={'Status':'Failed' if self.fail_phase==phase else 'Success','StandardOutputContent':json.dumps(output)}
        self.events.append(('agent',phase));return {'Command':{'CommandId':identifier}}
    def get_command_invocation(self,CommandId,**kw):return self.commands[CommandId]
    def stop_instances(self,InstanceIds):
        for i in InstanceIds:
            assert i in executor.STANDALONE
            assert any(k.startswith('operations/') for k,v in self.objects)
            self.instances[i]['State']['Name']='stopped';self.events.append(('stop',i))
    def start_instances(self,InstanceIds):
        for i in InstanceIds:self.instances[i]['State']['Name']='running';self.events.append(('start',i))
    def modify_instance_attribute(self,InstanceId,BlockDeviceMappings):
        assert InstanceId in self.workers
        for change in BlockDeviceMappings:
            assert change['Ebs']=={'DeleteOnTermination':False}
            self.instances[InstanceId]['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination']=False
        self.events.append(('retain',InstanceId))
    def suspend_processes(self,ScalingProcesses,**kw):
        existing={p['ProcessName'] for p in self.group['SuspendedProcesses']}
        self.group['SuspendedProcesses']=[{'ProcessName':p} for p in sorted(existing|set(ScalingProcesses))]
        self.events.append(('suspend',list(ScalingProcesses)))
    def resume_processes(self,ScalingProcesses,**kw):
        self.group['SuspendedProcesses']=[p for p in self.group['SuspendedProcesses'] if p['ProcessName'] not in ScalingProcesses]
        self.events.append(('resume',list(ScalingProcesses)))
    def update_nodegroup_config(self,scalingConfig,**kw):
        self.events.append(('capacity',copy.deepcopy(scalingConfig)))
        if scalingConfig['desiredSize']==0:
            for w in self.workers:
                for m in self.instances[w]['BlockDeviceMappings']:
                    if m['Ebs']['DeleteOnTermination']:self.volumes=[v for v in self.volumes if v['VolumeId']!=m['Ebs']['VolumeId']]
                self.instances[w]['State']['Name']='terminated'
            self.group['Instances']=[]
        else:
            assert not any(p['ProcessName']=='Launch' for p in self.group['SuspendedProcesses'])
            self.group['Instances']=self.members()
        self.capacity=copy.deepcopy(scalingConfig)
        return {'update':{'id':'update'}}
    def describe_instance_information(self,Filters):
        iid=Filters[0]['Values'][0]
        return {'InstanceInformationList':[{'InstanceId':iid,'PingStatus':'Online' if self.instances[iid]['State']['Name']=='running' else 'ConnectionLost'}]}
    def get_queue_attributes(self,**kw):return {'Attributes':{'ApproximateNumberOfMessages':'0','ApproximateNumberOfMessagesNotVisible':'0'}}


def create_operation(aws,identifier,direction,source=None):
    op={'pk':'OP#'+identifier,'id':identifier,'actor':'owner','plan_id':'approved','direction':direction,'state':'RUNNING','confirmed':True,
        'revision':0,'index':0,'started_at':int(time.time())}
    if direction=='DOWN':
        op['baseline']={'plan_id':'approved','kubernetes':aws.approved,'targets':[{'type':'MNG','id':'nodes','capacity':copy.deepcopy(aws.capacity)}]+[
          {'type':'EC2','id':i,'state':aws.instances[i]['State']['Name']} for i in executor.STANDALONE], 'resources':[]}
    else:op.update(snapshot=aws.table.data['OP#'+source]['snapshot'],source_operation=source)
    aws.table.put_item(Item=op);aws.table.put_item(Item={'pk':'LOCK','operation':identifier})
    return op


def drive(aws,identifier):
    for _ in range(150):
        r=executor.handler({'opid':identifier},None)
        if r['state']!='wait':return r
    raise AssertionError('State machine did not converge')


@pytest.fixture
def model(monkeypatch):
    aws=AWS()
    monkeypatch.setenv('HOST_READ_DOCUMENT','HostRead');monkeypatch.setenv('HOST_EXECUTE_DOCUMENT','HostRestore')
    monkeypatch.setenv('SNAPSHOT_BUCKET','bucket');monkeypatch.setenv('READ_DOCUMENT','Read');monkeypatch.setenv('AGENT_DOCUMENT','Execute')
    monkeypatch.setattr(executor,'table',lambda:aws.table);monkeypatch.setattr(executor,'client',lambda name:aws)
    return aws


def test_full_down_up_preserves_original_volumes_and_capacity(model):
    before=copy.deepcopy(model.volumes);original=copy.deepcopy(model.capacity)
    create_operation(model,'down','DOWN');assert drive(model,'down')['state']=='done'
    assert model.table.data['CONTROL']['state']=='OFF' and 'LOCK' not in model.table.data
    assert model.capacity['desiredSize']==0 and model.capacity['maxSize']==3
    assert model.volumes==before
    assert [v[1] for v in model.events if v[0]=='stop']==[executor.ec2_id(n) for n in ['itsm','zabbix','control']]
    create_operation(model,'up','UP','down');assert drive(model,'up')['state']=='done'
    assert model.capacity==original and model.volumes==before
    assert model.table.data['CONTROL']['state']=='ON'
    assert [v[1] for v in model.events if v[0]=='start'][0]==executor.HOST
    assert not model.group['SuspendedProcesses']


def test_originally_stopped_instance_stays_stopped(model):
    iid=executor.ec2_id('itsm');model.instances[iid]['State']['Name']='stopped'
    create_operation(model,'down','DOWN');drive(model,'down')
    create_operation(model,'up','UP','down');drive(model,'up')
    assert model.instances[iid]['State']['Name']=='stopped'
    assert ('start',iid) not in model.events


def test_failure_recovers_only_instances_stopped_by_operation(model):
    model.fail_phase='QUIESCE'
    create_operation(model,'down','DOWN');assert drive(model,'down')['state']=='failed'
    assert model.table.data['CONTROL']['state']=='RECOVERY_REQUIRED'
    assert model.table.data['LOCK']['operation']=='down'
    assert model.table.data['OP#down']['stop_intents']==[executor.ec2_id('itsm')]
    model.fail_phase=None
    create_operation(model,'up','UP','down');assert drive(model,'up')['state']=='done'
    assert [v[1] for v in model.events if v[0]=='start']==[executor.ec2_id('itsm')]
    assert model.table.data['CONTROL']['state']=='ON'


def test_capture_failure_never_stops_anything_and_releases_lock(model):
    model.fail_phase='SNAPSHOT';create_operation(model,'down','DOWN')
    assert drive(model,'down')['state']=='failed'
    assert model.table.data['CONTROL']['state']=='ON' and 'LOCK' not in model.table.data
    assert not any(e[0] in {'stop','capacity','retain','suspend'} for e in model.events)


def test_unconfirmed_or_unowned_execution_is_rejected(model):
    op=create_operation(model,'down','DOWN');op['confirmed']=False;model.table.put_item(Item=op)
    with pytest.raises(PermissionError):executor.handler({'opid':'down'},None)
    assert not model.events


def test_existing_suspended_process_is_not_resumed(model):
    model.group['SuspendedProcesses']=[{'ProcessName':'AZRebalance'}]
    create_operation(model,'down','DOWN');drive(model,'down')
    create_operation(model,'up','UP','down');drive(model,'up')
    assert model.group['SuspendedProcesses']==[{'ProcessName':'AZRebalance'}]


def test_protected_network_drift_blocks_success_without_repair(model):
    create_operation(model,'down','DOWN')
    while model.table.data['OP#down'].get('phase')!='stop:control':executor.handler({'opid':'down'},None)
    model.network=[{'AllocationId':'unexpected-change'}]
    assert drive(model,'down')['state']=='failed'
    assert model.table.data['CONTROL']['state']=='RECOVERY_REQUIRED'
    assert model.network==[{'AllocationId':'unexpected-change'}]


def test_operation_requires_confirmation_and_lock_excludes_concurrency(model,monkeypatch):
    monkeypatch.setattr(api,'table',lambda:model.table)
    monkeypatch.setenv('STATE_MACHINE_ARN','arn:aws:states:us-east-1:414053644145:stateMachine:cosmotech-power-operations')
    result={'actor':'owner','expires':int(time.time())+600,'direction':'DOWN','id':'plan','ready':True,'blockers':[], 'targets':[],'resources':[],'kubernetes':{}}
    body={'confirmation':'DESLIGAR LABORATÓRIO','idempotencyKey':'a19c54a7-2dd4-46df-8e55-e3e075a65449'}
    assert api.submit_operation(result,'owner',body)['statusCode']==400
    model.table.put_item(Item={'pk':'LOCK','operation':'another'})
    body['firstShutdownConsent']=True
    assert api.submit_operation(result,'owner',body)['statusCode']==409
    assert not model.events


def test_terminal_checkpoint_retry_finishes_control_and_lock(model):
    create_operation(model,'down','DOWN');drive(model,'down')
    model.table.put_item(Item={'pk':'LOCK','operation':'down'})
    model.table.put_item(Item={'pk':'CONTROL','state':'SHUTTING_DOWN','operation':'down'})
    assert executor.handler({'opid':'down'},None)['state']=='done'
    assert model.table.data['CONTROL']['state']=='OFF' and 'LOCK' not in model.table.data


def test_service_access_cannot_confirm_real_power_operation(model,monkeypatch):
    monkeypatch.setattr(api,'table',lambda:model.table)
    result={'actor':'private-site-service','expires':int(time.time())+600,'direction':'DOWN','id':'plan','ready':True,'blockers':[]}
    assert api.submit_operation(result,'private-site-service',{'confirmation':'DESLIGAR LABORATÓRIO','firstShutdownConsent':True})['statusCode']==403
    assert not model.events and 'LOCK' not in model.table.data
