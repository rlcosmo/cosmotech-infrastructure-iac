"""Prepared integration patch in a disposable checkout; primary is never deployed."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class PrimaryIntegrationKitTests(unittest.TestCase):
    def test_202_after_journal_commit_legacy_unchanged_conflict_and_failure(self):
        fixture=Path(os.environ.get('ORDERS_API_INTEGRATION_SOURCE','/tmp/dr-api-integration-test'))
        self.assertTrue((fixture/'src/orders/producer/durable.py').is_file(),'Prepared API fixture required')
        code=textwrap.dedent('''
            import json,os
            from orders.producer.handler import handler
            from orders.runtime.api import normalize_request
            from orders.shared.dr_outbox.outbox import Outbox
            class Store:
                def __init__(self):self.items={};self.available=True
                def put_if_absent(self,r):
                    if not self.available:raise ConnectionError('Injected store outage')
                    return self.items.setdefault(r['order_id'],r)
            class Queue:
                calls=0
                def send_message(self,**kw):self.calls+=1;return {'MessageId':'legacy'}
            store=Store();box=Outbox(store,clock=lambda:1000);q=Queue()
            order={'order_id':'kit-1','items':[{'sku':'LAB-SKU-001','quantity':1}]}
            event={'body':json.dumps(order)}
            os.environ['ORDERS_QUEUE_URL']='https://sqs.us-east-1.amazonaws.com/414053644145/aws-eks-istio-lab-dev-orders-eks'
            os.environ.pop('ORDERS_DURABLE_OUTBOX',None)
            assert handler(event,None,sqs_client=q)['statusCode']==202 and q.calls==1 and not store.items
            os.environ['ORDERS_DURABLE_OUTBOX']='true'
            assert handler(event,None,sqs_client=q,durable_outbox=box)['statusCode']==202
            assert handler(event,None,sqs_client=q,durable_outbox=box)['statusCode']==202
            assert len(store.items)==1 and q.calls==1
            other={'body':json.dumps({**order,'items':[{'sku':'LAB-SKU-001','quantity':2}]})}
            assert handler(other,None,sqs_client=q,durable_outbox=box)['statusCode']==409
            store.available=False
            assert handler(event,None,sqs_client=q,durable_outbox=box)['statusCode']==503
            assert 'order_id' not in normalize_request({'customer_id':'retry','items':[]})
            print('PASS: prepared primary producer durable hook; no SQS send with opt-in; legacy untouched')
        ''')
        env={**os.environ,'PYTHONPATH':str(fixture/'src'),'AWS_EC2_METADATA_DISABLED':'true'}
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS: prepared primary',result.stdout)
