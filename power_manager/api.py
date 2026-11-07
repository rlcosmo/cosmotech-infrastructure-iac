"""Authenticated planning API. Power execution remains fail-closed until certified."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from functools import lru_cache
from decimal import Decimal
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from .core import plan, digest, PHRASES
from .inventory import discover

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
    return {'statusCode': code, 'headers': {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'},
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


def route(event, actor, body):
    method, path = event['requestContext']['http']['method'], event['rawPath']
    if method == 'GET' and path == '/api/status':
        state = table().get_item(Key={'pk': 'CONTROL'}, ConsistentRead=True).get('Item', {'state': 'ON', 'active_operation': None})
        return response(200, {'state': state, 'execution_enabled': False, 'first_shutdown_requires_confirmation': True,
                              'message': 'Somente planejamento: recuperação e retenção aguardam validação. Nenhuma ação de desligamento está habilitada.'})
    if method == 'GET' and path == '/api/inventory':
        return response(200, get_inventory())
    if method == 'POST' and path == '/api/plan':
        direction = body.get('direction')
        if direction not in PHRASES:
            return response(400, {'error': 'Direção inválida'})
        inventory = get_inventory(refresh=True)
        state = table().get_item(Key={'pk': 'CONTROL'}, ConsistentRead=True).get('Item', {})
        snapshot = state.get('snapshot')
        result = plan(inventory, direction, snapshot)
        result.update({'id': str(uuid.uuid4()), 'actor': actor, 'expires': int(time.time()) + 600})
        table().put_item(Item={'pk': 'PLAN#' + result['id'], 'actor': actor, 'expires': result['expires'], 'value': json.dumps(result, default=str)},
                         ConditionExpression=Attr('pk').not_exists())
        table().put_item(Item={'pk': 'AUDIT#' + str(time.time_ns()) + '#' + uuid.uuid4().hex, 'actor': actor, 'action': 'PLAN_' + direction,
                              'plan_id': result['id'], 'ready': result['ready'], 'timestamp': int(time.time())})
        return response(200, result)
    if method == 'POST' and path == '/api/operations':
        entry = table().get_item(Key={'pk': 'PLAN#' + str(body.get('planId', ''))}, ConsistentRead=True).get('Item')
        if not entry or entry['actor'] != actor or int(entry['expires']) <= time.time():
            return response(409, {'error': 'Plano ausente, expirado ou de outro usuário'})
        result = json.loads(entry['value'])
        if body.get('confirmation') != PHRASES[result['direction']]:
            return response(400, {'error': 'Confirmação explícita obrigatória'})
        if result['blockers']:
            return response(409, {'error': 'Pré-requisitos não atendidos', 'blockers': result['blockers']})
        # No environment flag can bypass certification. A reviewed implementation and
        # successful recovery evidence are required before granting mutation IAM.
        return response(409, {'error': 'Execução não certificada; não há permissão IAM de parada neste backend'})
    return response(404, {'error': 'Rota não encontrada'})


def handler(event, context):
    try:
        actor, body = authenticate(event)
        return route(event, actor, body)
    except PermissionError:
        return response(401, {'error': 'Não autorizado'})
    except (ValueError, KeyError, TypeError):
        return response(400, {'error': 'Requisição inválida'})
    except ClientError as exc:
        print(json.dumps({'event': 'aws_error', 'code': exc.response['Error']['Code'], 'request_id': getattr(context, 'aws_request_id', None)}))
        return response(503, {'error': 'AWS indisponível ou permissão insuficiente; plano bloqueado'})
