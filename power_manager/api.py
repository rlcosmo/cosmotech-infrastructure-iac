"""Authenticated planning API. Power execution remains fail-closed until certified."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from functools import lru_cache
from contextvars import ContextVar
from decimal import Decimal
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from boto3.dynamodb.conditions import Attr
from .core import plan, digest, PHRASES
from .inventory import discover

CORRELATION = ContextVar('power_correlation',default=None)

CONFIG = Config(connect_timeout=3, read_timeout=10, retries={'mode': 'standard', 'total_max_attempts': 2})


@lru_cache
def session():
    return boto3.Session(region_name='us-east-1')


@lru_cache
def table():
    return session().resource('dynamodb', config=CONFIG).Table(os.environ['TABLE'])


@lru_cache
def signing_secret():
    return session().client('ssm', config=CONFIG).get_parameter(Name=os.environ['SIGNING_PARAMETER'], WithDecryption=True)['Parameter']['Value']


def response(code, value):
    correlation=CORRELATION.get()
    headers={'content-type':'application/json; charset=utf-8','cache-control':'no-store'}
    if correlation:
        headers['x-correlation-id']=correlation
        value={**value,'correlation_id':correlation}
    return {'statusCode': code, 'headers': headers,
            'body': json.dumps(value, default=lambda o: int(o) if isinstance(o, Decimal) and o % 1 == 0 else str(o), ensure_ascii=False)}


def authenticate(event, now=None):
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    timestamp, actor, signature = (headers.get(k, '') for k in ('x-power-timestamp', 'x-power-actor', 'x-power-signature'))
    if not timestamp.isdigit() or abs((now or time.time()) - int(timestamp)) > 60 or not actor or len(actor) > 256:
        raise PermissionError('Autenticação inválida')
    body = event.get('body') or ''
    if event.get('isBase64Encoded'):
        body = base64.b64decode(body).decode()
    method = event['requestContext']['http']['method']
    path = event['rawPath']
    content = '\n'.join((timestamp, method, path, hashlib.sha256(body.encode()).hexdigest(), actor))
    expected = hmac.new(signing_secret().encode(), content.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise PermissionError('Autenticação inválida')
    return actor, json.loads(body) if body else {}


def get_inventory(refresh=False):
    cached = table().get_item(Key={'pk': 'INVENTORY'}, ConsistentRead=True).get('Item')
    if not refresh and cached and int(cached['expires']) > int(time.time()):
        return json.loads(cached['value'])
    inventory = discover(session())
    table().put_item(Item={'pk': 'INVENTORY', 'expires': int(time.time()) + 60, 'value': json.dumps(inventory, default=str)})
    return inventory


def complete_plan(entry):
    result=json.loads(entry['value'])
    if result.get('pending'):
        from .executor import client, get_json
        reports=[]
        commands=result.get('commands',{'kube':result.get('command_id')})
        for name,identifier in commands.items():
            iid='i-0b33fdd51332e04e5' if name=='kube' else name
            try:invocation=client('ssm').get_command_invocation(CommandId=identifier,InstanceId=iid)
            except client('ssm').exceptions.InvocationDoesNotExist:return result
            if invocation['Status'] in ('Pending','InProgress','Delayed'):return result
            if invocation['Status']!='Success':reports.append((name,None))
            else:reports.append((name,json.loads(invocation['StandardOutputContent'].strip().splitlines()[-1])))
        result['pending']=False;result['host_snapshots']={};result['warnings']=[]
        for name,report in reports:
            if report is None:
                result['blockers'].append('Pré-flight seguro falhou: '+name);continue
            if name=='kube':
                ref={'key':'plans/'+result['id']+'/kubernetes.json','version':report['version']}
                kube=get_json(ref);result['kubernetes']=ref;result['blockers'].extend(kube['blockers'])
                safe=kube.get('safe_scale_test',{})
                if not safe.get('down') or not safe.get('up') or safe.get('mutated') is not False:result['blockers'].append('Dry-run DOWN/UP não comprovado')
                result['kubernetes_summary']={'workloads':len(kube['workloads']),'pvs':len(kube['pvs']),'pvcs':len(kube['pvcs']),'hpa':len(kube['hpa']),'safe_test':safe}
            else:
                result['host_snapshots'][name]=report['reference'];result['warnings'].extend(report.get('warnings',[]))
                if not report.get('safe_restore_validated'):result['blockers'].append('Restauração de processos não verificada: '+name)
        if not result.get('kubernetes'):result['blockers'].append('Snapshot Kubernetes ausente')
        result['ready']=not result['blockers'];entry['value']=json.dumps(result,default=str);table().put_item(Item=entry)
    return result


def sync_control(control):
    from .executor import client, fail
    lock=table().get_item(Key={'pk':'LOCK'},ConsistentRead=True).get('Item')
    if lock:
        op=table().get_item(Key={'pk':'OP#'+lock['operation']},ConsistentRead=True).get('Item')
        if not op and time.time()-int(lock.get('created_at',0))>120:
            table().delete_item(Key={'pk':'LOCK'},ConditionExpression=Attr('operation').eq(lock['operation']))
        elif op and op['state']=='RUNNING' and op.get('execution_arn'):
            try:state=client('stepfunctions').describe_execution(executionArn=op['execution_arn'])['status']
            except client('stepfunctions').exceptions.ExecutionDoesNotExist:
                if time.time()-int(op['started_at'])>120 and not op.get('snapshot'):fail(op,'Execution was not started; no power mutation occurred')
            else:
                if state in ('FAILED','TIMED_OUT','ABORTED'):fail(op,'State machine '+state+'; preserved snapshot can be recovered')
        control=table().get_item(Key={'pk':'CONTROL'},ConsistentRead=True).get('Item',control)
    return control


def operation_view(identifier):
    result=table().get_item(Key={'pk':'OP#'+identifier},ConsistentRead=True).get('Item')
    if not result:return None
    from .executor import build_steps
    view={k:result.get(k) for k in ['id','direction','state','phase','index','started_at','updated_at','error','snapshot','execution_arn','history','differences']}
    view['total_steps']=len(build_steps(None,result['direction']))
    return view


def submit_operation(result,actor,body):
    from .executor import client
    if actor=='private-site-service':return response(403,{'error':'Confirmação exige sessão pessoal autenticada'})
    from .inventory import discover
    if result['actor']!=actor or result['expires']<=time.time():return response(409,{'error':'Plano expirado ou de outro usuário'})
    if body.get('confirmation')!=PHRASES[result['direction']]:return response(400,{'error':'Confirmação explícita obrigatória'})
    if result.get('pending') or not result.get('ready'):return response(409,{'error':'Plano não está pronto','blockers':result['blockers']})
    if result['direction']=='DOWN' and body.get('firstShutdownConsent') is not True:return response(400,{'error':'Confirme explicitamente o desligamento e o plano de recuperação'})
    try:opid=str(uuid.UUID(body['idempotencyKey']))
    except (ValueError,KeyError,TypeError):return response(400,{'error':'Idempotency key UUID obrigatória'})
    existing=table().get_item(Key={'pk':'OP#'+opid},ConsistentRead=True).get('Item')
    if existing:
        if existing['actor']!=actor or existing['plan_id']!=result['id']:return response(409,{'error':'Idempotency key já utilizada'})
        if existing['state']=='RUNNING':
            client('stepfunctions').start_execution(stateMachineArn=os.environ['STATE_MACHINE_ARN'],name=opid,input=json.dumps({'opid':opid}))
        return response(202,operation_view(opid))
    if result['direction']=='DOWN':
        version=os.environ.get('AGENT_HASH')
        if not version or result.get('agent_hash')!=version:
            return response(409,{'error':'A versão do executor mudou; prepare um novo plano','error_code':'PLAN_VERSION_CHANGED'})
        certificate=table().get_item(Key={'pk':'CERTIFICATION'},ConsistentRead=True).get('Item',{})
        if not certificate.get('complete') or certificate.get('agent_hash')!=version:
            return response(409,{'error':'Certificação de segurança pendente; desligamento bloqueado','error_code':'CERTIFICATION_PENDING'})
    control=table().get_item(Key={'pk':'CONTROL'},ConsistentRead=True).get('Item',{'state':'ON'})
    if result['direction']=='DOWN' and control['state']!='ON':return response(409,{'error':'Laboratório não está no estado ON'})
    if result['direction']=='UP' and control['state'] not in ('OFF','RECOVERY_REQUIRED'):return response(409,{'error':'Recuperação não está disponível neste estado'})
    lock={'pk':'LOCK','operation':opid,'actor':actor,'plan_id':result['id'],'created_at':int(time.time())}
    try:
        if result['direction']=='UP' and control['state']=='RECOVERY_REQUIRED':
            table().put_item(Item=lock,ConditionExpression=Attr('pk').not_exists()|Attr('operation').eq(control['operation'])|Attr('operation').eq(opid))
        else:table().put_item(Item=lock,ConditionExpression=Attr('pk').not_exists()|Attr('operation').eq(opid))
    except table().meta.client.exceptions.ConditionalCheckFailedException:
        return response(409,{'error':'Outra operação está em andamento'})
    op={'pk':'OP#'+opid,'id':opid,'actor':actor,'plan_id':result['id'],'direction':result['direction'],
        'state':'RUNNING','phase':'queued','index':0,'revision':0,'started_at':int(time.time()),'confirmed':True,'created_correlation_id':CORRELATION.get(),
        'execution_arn':os.environ['STATE_MACHINE_ARN'].replace(':stateMachine:',':execution:')+':'+opid}
    if result['direction']=='DOWN':
        # Source data is from the actor-bound plan. Capacity is rechecked by the worker
        # and fresh network/volume snapshots are stored before any external mutation.
        op['baseline']={'plan_id':result['id'],'targets':result['targets'],'resources':result['resources'],'kubernetes':result['kubernetes'],'host_snapshots':result['host_snapshots']}
    else:
        if not control.get('snapshot'):
            table().delete_item(Key={'pk':'LOCK'},ConditionExpression=Attr('operation').eq(opid))
            return response(409,{'error':'Snapshot original ausente'})
        op.update(snapshot=control['snapshot'],source_operation=control['source_operation'])
    table().put_item(Item=op,ConditionExpression=Attr('pk').not_exists())
    next_control={'pk':'CONTROL','state':'SHUTTING_DOWN' if result['direction']=='DOWN' else 'STARTING','operation':opid}
    if op.get('snapshot'):next_control.update(snapshot=op['snapshot'],source_operation=op['source_operation'])
    table().put_item(Item=next_control)
    client('stepfunctions').start_execution(stateMachineArn=os.environ['STATE_MACHINE_ARN'],name=opid,input=json.dumps({'opid':opid}))
    table().put_item(Item={'pk':'AUDIT#'+str(time.time_ns())+'#'+opid,'actor':actor,'action':result['direction'],
      'plan_id':result['id'],'operation_id':opid,'confirmed':True,'correlation_id':CORRELATION.get(),'timestamp':int(time.time())})
    return response(202,operation_view(opid))


def route(event,actor,body):
    method,path=event['requestContext']['http']['method'],event['rawPath']
    if method=='GET' and path=='/api/status':
        control=table().get_item(Key={'pk':'CONTROL'},ConsistentRead=True).get('Item',{'state':'ON'})
        control=sync_control(control)
        certification=table().get_item(Key={'pk':'CERTIFICATION'},ConsistentRead=True).get('Item',{})
        return response(200,{'state':control,'operation':operation_view(control['operation']) if control.get('operation') else None,
          'execution_enabled':bool(certification.get('complete')) and certification.get('agent_hash')==os.environ.get('AGENT_HASH'),
          'first_shutdown_requires_confirmation':True,'certification':certification})
    if method=='GET' and path=='/api/inventory':return response(200,get_inventory())
    if method=='GET' and path.startswith('/api/plans/'):
        entry=table().get_item(Key={'pk':'PLAN#'+path.rsplit('/',1)[1]},ConsistentRead=True).get('Item')
        if not entry or entry['actor']!=actor:return response(404,{'error':'Plano não encontrado'})
        return response(200,complete_plan(entry))
    if method=='GET' and path.startswith('/api/operations/'):
        op=table().get_item(Key={'pk':'OP#'+path.rsplit('/',1)[1]},ConsistentRead=True).get('Item')
        if not op or op['actor']!=actor:return response(404,{'error':'Operação não encontrada'})
        return response(200,operation_view(op['id']))
    if method=='POST' and path=='/api/plan':
        from .executor import client
        direction=body.get('direction')
        if direction not in PHRASES:return response(400,{'error':'Direção inválida'})
        inventory=get_inventory(refresh=True)
        control=table().get_item(Key={'pk':'CONTROL'},ConsistentRead=True).get('Item',{'state':'ON'})
        certification=table().get_item(Key={'pk':'CERTIFICATION'},ConsistentRead=True).get('Item',{})
        control=sync_control(control)
        blockers=list(inventory['errors']) if direction=='DOWN' else []
        if direction=='DOWN' and (not certification.get('complete') or certification.get('agent_hash')!=os.environ.get('AGENT_HASH')):
            blockers.append('Certificação AWS/recuperação do executor pendente para esta versão')
        if direction=='DOWN' and control['state']!='ON':blockers.append('Laboratório não está no estado ON')
        if direction=='UP' and not control.get('snapshot'):blockers.append('Nenhum snapshot original de desligamento disponível')
        if direction=='UP' and control['state'] not in ('OFF','RECOVERY_REQUIRED'):blockers.append('Sem desligamento ou recuperação pendente')
        result={'id':str(uuid.uuid4()),'direction':direction,'actor':actor,'expires':int(time.time())+600,'agent_hash':os.environ.get('AGENT_HASH'),
          'confirmation':PHRASES[direction],'blockers':blockers,'ready':False,'pending':False,
          'resources':inventory['resources'],'targets':[r for r in inventory['resources'] if r.get('action') in ('stop','scale')],
          'protected':[r for r in inventory['resources'] if r['classification']!='LAB'],
          'recovery':['Iniciar o host de controle independente','Restaurar capacidades min/max/desired e processos ASG registrados',
            'Restaurar réplicas, Istio, persistência e controladores; atualizar estado Argo CD sem alterar Services/Ingress',
            'Comparar saúde, volumes e fingerprint das configurações de rede'],
          'limitations':['Nós Spot e IPs internos são efêmeros; endpoints públicos protegidos permanecem',
            'Volumes raiz serão preservados com DeleteOnTermination=false e permanecerão armazenados após substituição de nós',
            'Processos em memória e mensagens em trânsito têm limitações; filas precisam drenar antes de remover nós']}
        if direction=='DOWN' and not blockers:
            command=client('ssm').send_command(InstanceIds=['i-0b33fdd51332e04e5'],DocumentName=os.environ['READ_DOCUMENT'],
             Parameters={'Phase':['SAFE_TEST'],'PlanId':[result['id']],'OperationId':[result['id']]},TimeoutSeconds=600,Comment='Read-only power plan and DOWN/UP server dry-run')
            commands={'kube':command['Command']['CommandId']}
            for target in result['targets']:
                if target['type']=='EC2' and target['state']=='running':
                    sent=client('ssm').send_command(InstanceIds=[target['id']],DocumentName=os.environ['HOST_READ_DOCUMENT'],Parameters={'Mode':['SAFE_TEST'],'PlanId':[result['id']],'OperationId':[result['id']]},TimeoutSeconds=600,Comment='Read-only application recovery preflight')
                    commands[target['id']]=sent['Command']['CommandId']
            result.update(pending=True,commands=commands)
        if direction=='UP' and not blockers:
            from .executor import get_json
            source=get_json(control['snapshot'])
            if source.get('schema_version')!=1:result['blockers'].append('Formato de snapshot não suportado; recuperação exige revisão')
            else:
                result['targets']=source['targets'];result['resources']=source['resources'];result['original_snapshot']=control['snapshot']
                original_op=table().get_item(Key={'pk':'OP#'+control['source_operation']},ConsistentRead=True)['Item']
                result['restore_instances']=original_op.get('stop_intents',[])
            result['ready']=not result['blockers']
        entry={'pk':'PLAN#'+result['id'],'actor':actor,'expires':result['expires'],'value':json.dumps(result,default=str)}
        table().put_item(Item=entry,ConditionExpression=Attr('pk').not_exists())
        return response(200,result)
    if method=='POST' and path=='/api/operations':
        entry=table().get_item(Key={'pk':'PLAN#'+str(body.get('planId',''))},ConsistentRead=True).get('Item')
        if not entry or entry['actor']!=actor:return response(409,{'error':'Plano ausente ou de outro usuário'})
        return submit_operation(complete_plan(entry),actor,body)
    return response(404,{'error':'Rota não encontrada'})


def handle_request(event, context):
    try:
        # Direct Lambda invocation is IAM-authorized. API Gateway always supplies
        # requestContext, so a browser body cannot select this administrative path.
        # No visitor impersonation or Sites service-access consent is used.
        if event.get('source')=='cosmotech.power.iam-admin' and 'requestContext' not in event:
            run_id=str(event.get('run_id',''))
            if not re.fullmatch(r'[0-9]{1,24}-[0-9]{1,4}',run_id):raise PermissionError()
            method,path=event.get('method'),event.get('path','')
            if not ((method=='GET' and (path in ('/api/status','/api/inventory') or re.fullmatch(r'/api/(plans|operations)/[0-9a-f-]{36}',path))) or (method=='POST' and path in ('/api/plan','/api/operations'))):raise PermissionError()
            if event.get('authorization')!='OWNER_AUTHORIZED_CONTROLLED_CYCLE':raise PermissionError()
            return route({'requestContext':{'http':{'method':method}},'rawPath':path},'github-actions:'+run_id,event.get('payload',{}))
        actor, body = authenticate(event)
        return route(event, actor, body)
    except PermissionError:
        return response(401, {'error': 'Não autorizado','error_code':'UNAUTHORIZED'})
    except (ValueError, KeyError, TypeError):
        return response(400, {'error': 'Requisição inválida','error_code':'INVALID_REQUEST'})
    except BotoCoreError:
        return response(503, {'error': 'Conexão AWS indisponível; nenhuma nova operação pode ser confirmada','error_code':'AWS_CONNECTION_ERROR'})
    except ClientError as exc:
        print(json.dumps({'event': 'aws_error', 'code': exc.response['Error']['Code'], 'request_id': getattr(context, 'aws_request_id', None),'correlation_id':CORRELATION.get(),'operation':exc.operation_name,'aws_request_id':exc.response.get('ResponseMetadata',{}).get('RequestId')}))
        return response(503, {'error': 'AWS indisponível ou permissão insuficiente; plano bloqueado','error_code':exc.response['Error']['Code']})


def handler(event,context):
    headers={k.lower():v for k,v in event.get('headers',{}).items()}
    incoming=headers.get('x-power-correlation-id','')
    correlation=incoming if re.fullmatch(r'[0-9a-fA-F-]{36}',incoming) else str(uuid.uuid4())
    token=CORRELATION.set(correlation);started=time.monotonic();result=None
    try:
        result=handle_request(event,context)
        return result
    except Exception as exc:
        # Never log headers, bodies, secret values or exception strings.
        print(json.dumps({'event':'unexpected_error','type':type(exc).__name__,'correlation_id':correlation,'request_id':getattr(context,'aws_request_id',None)}))
        result=response(500,{'error':'Erro interno do serviço de controle; operação não confirmada','error_code':'INTERNAL_ERROR'})
        return result
    finally:
        print(json.dumps({'event':'power_request','correlation_id':correlation,'request_id':getattr(context,'aws_request_id',None),'status':result.get('statusCode') if result else 500,'duration_ms':int((time.monotonic()-started)*1000)}))
        CORRELATION.reset(token)
