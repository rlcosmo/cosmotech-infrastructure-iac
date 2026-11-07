import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('direct_sdk',Path(__file__).parents[1]/'ops/iac-portal/direct_sdk.py')
sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
CREDS={'AccessKeyId':'ASIA-test-only','SecretAccessKey':'test-only','SessionToken':'test-only'}

class SdkInventoryTests(unittest.TestCase):
    def test_parameter_casing_and_lists_follow_service_schema(self):
        members={'OwnerIds':SimpleNamespace(type_name='list'),'clusterName':SimpleNamespace(type_name='string'),'MaxResults':SimpleNamespace(type_name='integer')}
        self.assertEqual(sdk.parameters(['--owner-ids','self','--cluster-name','test','--max-results','1000'],members),{'OwnerIds':['self'],'clusterName':'test','MaxResults':1000})

    def test_pagination_combines_every_page_and_reuses_client(self):
        client=Mock()
        client.meta.method_to_api_mapping={'describe_volumes':'DescribeVolumes'}
        client.meta.service_model.operation_model.return_value.input_shape=None
        client.can_paginate.return_value=True
        client.get_paginator.return_value.paginate.return_value=[{'Volumes':[{'VolumeId':'vol-1'}],'NextToken':'private-token'}, {'Volumes':[{'VolumeId':'vol-2'}]}]
        session=Mock();session.client.return_value=client
        transport=sdk.InventorySdk(CREDS,session_factory=Mock(return_value=session))
        self.assertEqual(transport('us-east-1','ec2','describe-volumes'),{'Volumes':[{'VolumeId':'vol-1'},{'VolumeId':'vol-2'}]})
        transport('us-east-1','ec2','describe-volumes')
        session.client.assert_called_once()
        transport.close();client.close.assert_called_once()

    def test_mutations_and_static_credentials_are_rejected(self):
        with self.assertRaises(ValueError):sdk.InventorySdk(dict(CREDS,AccessKeyId='AKIA-test-only'),session_factory=Mock())
        transport=sdk.InventorySdk(CREDS,session_factory=Mock())
        with self.assertRaises(ValueError):transport('us-east-1','ec2','terminate-instances')
