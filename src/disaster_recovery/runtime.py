"""Standby-only durable Orders API / publisher. Default is paused, never primary.

No boto3 call occurs at import. Service identity and destination names are checked
before startup; HTTP responses/logs do not include SDK errors, tokens or payloads.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import time

from .outbox import Conflict, Outbox


def activation(path, *, now=None):
    now = time.time() if now is None else now
    try:
        v = json.loads(Path(path).read_text())
        if (v['mode'] == 'run' and v['region'] == 'us-east-2'
                and type(v['epoch']) is int and v['epoch'] > 0
                and type(v['expires_at']) in {int,float} and now+15 < v['expires_at'] <= now+300):
            return v['epoch']
    except (ValueError, KeyError, OSError, TypeError):
        pass
    return None


def accept_request(outbox, payload, *, active):
    if not active:
        return 503, {'error':'DR writer activation lease absent/expired'}
    try:
        r = outbox.accept(payload)
        return 202, {'order_id':r['order_id'], 'status':'PENDING', 'acceptance':'durable-journal'}
    except Conflict:
        return 409, {'error':'Order ID conflicts with its accepted payload'}
    except (ValueError, TypeError, KeyError, AttributeError):
        return 400, {'error':'Explicit order_id and valid items required'}
    except Exception:
        return 503, {'error':'Durable journal unavailable; order not acknowledged'}


def aws_runtime():
    import boto3
    from botocore.config import Config
    from .dynamo_outbox import DynamoOutbox
    region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    table = os.environ.get('DR_OUTBOX_TABLE')
    if region != 'us-east-2' or table != 'cosmotech-dr-outbox':
        raise ValueError('Runtime is restricted to the isolated Ohio outbox')
    config = Config(connect_timeout=2, read_timeout=3, retries={'mode':'standard','total_max_attempts':2})
    session = boto3.Session(region_name=region)
    identity = session.client('sts', config=config).get_caller_identity()
    if identity['Account'] != '414053644145' or ':assumed-role/cosmotech-dr-' not in identity['Arn']:
        raise ValueError('Require a temporary dedicated DR role; root/static user rejected')
    resource = session.resource('dynamodb', config=config)
    return session, config, Outbox(DynamoOutbox(resource.Table(table))), resource


def serve(outbox, business_table, activation_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def response(self, status, value):
            body = json.dumps(value,default=lambda v:int(v)).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

        def do_GET(self):
            if self.path in {'/health/live','/health/ready'}:
                ready = self.path.endswith('live') or activation(activation_path) is not None
                if ready and self.path.endswith('ready'):
                    try:outbox.store.table.get_item(Key={'order_id':'__dr_health__'},ConsistentRead=True)
                    except Exception:ready=False
                return self.response(200 if ready else 503, {'ready':ready})
            if self.path.startswith('/orders/'):
                from urllib.parse import unquote
                key = unquote(self.path[len('/orders/'):])
                if not key or len(key)>128 or key.startswith(('INVENTORY#','IDEMPOTENCY#')):
                    return self.response(400, {'error':'Invalid order ID'})
                try:
                    item = business_table.get_item(Key={'order_id':key}, ConsistentRead=True).get('Item')
                    return self.response(200 if item else 404, item or {'error':'Order not found'})
                except Exception:
                    return self.response(503, {'error':'Order storage unavailable'})
            return self.response(404, {'error':'Not found'})

        def do_POST(self):
            if self.path != '/orders':return self.response(404, {'error':'Not found'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0<size<=256*1024:raise ValueError('Size')
                payload = json.loads(self.rfile.read(size))
            except (ValueError,TypeError):return self.response(400, {'error':'Invalid request'})
            return self.response(*accept_request(outbox, payload, active=activation(activation_path)))

    class Server(HTTPServer):
        def get_request(self):
            sock,address=super().get_request();sock.settimeout(5);return sock,address
    Server(('0.0.0.0',8000),Handler).serve_forever()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['api','publisher','worker','metrics'])
    args=parser.parse_args()
    session,config,outbox,resource=aws_runtime()
    control=os.environ.get('DR_ACTIVATION_FILE','/dr-control/activation.json')
    if args.mode=='api':
        return serve(outbox,resource.Table('cosmotech-dr-orders-restored'),control)
    queue_url=os.environ.get('DR_QUEUE_URL')
    if queue_url!='https://sqs.us-east-2.amazonaws.com/414053644145/cosmotech-dr-passive-orders':
        raise ValueError('Only secondary queue accepted')
    if args.mode=='worker':
        from botocore.config import Config
        from orders.consumer.processing import DynamoDBOrderProcessor
        from orders.runtime.worker import process_message
        from orders.runtime.controlled_consumer import poll_once
        from orders.runtime.health import WorkerHealth, start_health_server
        from orders.runtime.worker_metrics import WorkerMetrics
        import signal
        sqs=session.client('sqs',config=config.merge(Config(read_timeout=25)))
        processor=DynamoDBOrderProcessor('cosmotech-dr-orders-restored',dynamodb_resource=resource,key_schema='legacy')
        health=WorkerHealth();metrics=WorkerMetrics(os.getenv('RELEASE_VERSION','unpublished'),'dr')
        start_health_server(health,metrics=metrics)
        stopping=[False]
        def stop(*args):stopping[0]=True
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        while not stopping[0]:
            epoch=activation(control)
            result=poll_once(sqs,queue_url,lambda item:process_message(item,processor),
                lambda:stopping[0] or epoch is None or activation(control)!=epoch,health,metrics)
            if result in {'drained','poll_error'}:time.sleep(1)
        return
    sqs=session.client('sqs',config=config)
    if args.mode=='metrics':
        from .telemetry import render_outbox, verified_report
        class Metrics(BaseHTTPRequestHandler):
            def log_message(self,*args):return
            def do_GET(self):
                if self.path=='/health/live':
                    self.send_response(200);self.end_headers();self.wfile.write(b'OK');return
                if self.path!='/metrics':self.send_error(404);return
                try:
                    rows=list(outbox.store.records());now_ms=int(time.time()*1000);report=None
                    report_path=os.getenv('DR_RECONCILIATION_REPORT')
                    if report_path:
                        try:
                            path=Path(report_path)
                            if path.stat().st_size<=1024*1024:
                                report=verified_report(json.loads(path.read_text()),rows,now_ms)
                        except (OSError,ValueError,KeyError):pass
                    text=render_outbox(rows,now_ms=now_ms,report=report)
                    text+=f'cosmotech_dr_outbox_active{{region="us-east-2"}} {int(activation(control) is not None)}\n'
                    attr=sqs.get_queue_attributes(QueueUrl=queue_url,AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible'])['Attributes']
                    for name,key in [('sqs_visible','ApproximateNumberOfMessages'),('sqs_inflight','ApproximateNumberOfMessagesNotVisible')]:
                        text+=f'cosmotech_dr_{name}{{region="us-east-2"}} {int(attr[key])}\n'
                    body=text.encode();self.send_response(200);self.send_header('Content-Type','text/plain; version=0.0.4')
                    self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
                except Exception:self.send_error(503,'Operational observations unavailable')
        return HTTPServer(('0.0.0.0',9090),Metrics).serve_forever()

    class Queue:
        def send_message(self,**kwargs):return sqs.send_message(QueueUrl=queue_url,**kwargs)
    while True:
        epoch=activation(control)
        if epoch:
            try:
                outbox.sweep(Queue(),epoch=epoch,lease_live=lambda:activation(control)==epoch)
            except Exception as error:
                print(json.dumps({'event':'outbox_publish_retry','error_type':type(error).__name__}),flush=True)
        time.sleep(2)

if __name__=='__main__':
    main()
