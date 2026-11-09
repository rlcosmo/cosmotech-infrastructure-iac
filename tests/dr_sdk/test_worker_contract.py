"""Integration with the unchanged, immutable inspected worker checkout; Moto only."""
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import boto3
from moto import mock_aws

from src.disaster_recovery.outbox import Outbox
from src.disaster_recovery.dynamo_outbox import DynamoOutbox
from test_dynamo_outbox import create_table


@mock_aws
class ExistingWorkerIntegration(unittest.TestCase):
    def setUp(self):
        root=Path(os.environ.get('ORDERS_WORKER_SOURCE','/tmp/dr-audit-cosmotech-orders-worker'))
        if not (root/'src/orders/consumer/processing.py').is_file():
            self.fail('Pinned worker checkout required for the DR integration suite')
        sys.path.insert(0,str(root/'src'))
        module=importlib.import_module('orders.consumer.processing')
        self.assertTrue(Path(module.__file__).is_relative_to(root))
        self.processor_class=module.DynamoDBOrderProcessor
        self.ddb=boto3.resource('dynamodb',region_name='us-east-2')
        self.business=self.ddb.create_table(TableName='cosmotech-dr-orders-restored',BillingMode='PAY_PER_REQUEST',
            KeySchema=[{'AttributeName':'order_id','KeyType':'HASH'}],AttributeDefinitions=[{'AttributeName':'order_id','AttributeType':'S'}])
        self.business.put_item(Item={'order_id':'INVENTORY#LAB','stock':{'LAB-SKU-001':100}})
        self.table=create_table(self.ddb);self.store=DynamoOutbox(self.table)
        self.now=1000;self.outbox=Outbox(self.store,clock=lambda:self.now)
        self.order={'order_id':'dr-integration-one','items':[{'sku':'LAB-SKU-001','quantity':1}]}
        self.processor=self.processor_class(self.business.name,dynamodb_resource=self.ddb,key_schema='legacy')

    def test_uncertain_sqs_send_and_redelivery_do_not_double_reserve_inventory(self):
        sqs=boto3.client('sqs',region_name='us-east-2');url=sqs.create_queue(QueueName='dr-test')['QueueUrl']
        class Q:
            def send_message(inner,**args):return sqs.send_message(QueueUrl=url,**args)
        self.outbox.accept(self.order)
        with patch.object(self.store,'sent',side_effect=ConnectionError('Crash after send')):
            with self.assertRaises(ConnectionError):self.outbox.publish(self.order['order_id'],Q(),epoch=1)
        self.now+=61;self.outbox.publish(self.order['order_id'],Q(),epoch=2)
        deliveries=sqs.receive_message(QueueUrl=url,MaxNumberOfMessages=10)['Messages']
        self.assertEqual(len(deliveries),2)
        for item in deliveries:
            event=json.loads(item['Body']);self.processor.persist(event['order'],event['created_at'])
        self.assertEqual(self.business.get_item(Key={'order_id':'INVENTORY#LAB'})['Item']['stock']['LAB-SKU-001'],99)
        self.assertEqual(self.business.get_item(Key={'order_id':self.order['order_id']})['Item']['status'],'COMPLETED')

    def test_restart_after_transactional_reservation_resumes_without_repeating_effect(self):
        record=self.outbox.accept(self.order);event=json.loads(record['event_json'])
        with patch.object(self.processor,'fulfill',side_effect=ConnectionError('Dependency unavailable')):
            with self.assertRaises(ConnectionError):self.processor.persist(event['order'],event['created_at'])
        self.assertEqual(self.business.get_item(Key={'order_id':self.order['order_id']})['Item']['status'],'RESERVED')
        fresh=self.processor_class(self.business.name,dynamodb_resource=self.ddb,key_schema='legacy')
        fresh.persist(event['order'],event['created_at'])
        fresh.persist(event['order'],event['created_at'])
        self.assertEqual(self.business.get_item(Key={'order_id':'INVENTORY#LAB'})['Item']['stock']['LAB-SKU-001'],99)
