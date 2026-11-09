"""Exercise real presigning with fake credentials and mocked HTTPS; no AWS calls."""
import base64
import importlib.util
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from test_direct_iac_comparison import module


@unittest.skipUnless(importlib.util.find_spec('botocore'),'SDK checked by scoped governance CI')
class KubernetesReadTests(unittest.TestCase):
    def test_get_only_token_bound_to_cluster_pagination_and_verified_ca(self):
        from botocore.credentials import ReadOnlyCredentials
        sdk=module('direct_sdk')
        transport=sdk.InventorySdk({'AccessKeyId':'ASIA-test-only','SecretAccessKey':'test','SessionToken':'test'},session_factory=Mock())
        transport._local.session=Mock()
        transport._local.session.get_credentials.return_value.get_frozen_credentials.return_value=ReadOnlyCredentials('ASIA-test-only','test','test')
        details={'cluster':{'endpoint':'https://example.eks.amazonaws.com','certificateAuthority':{'data':base64.b64encode(b'FAKE-CA').decode()}}}
        pages=[{'items':[{'id':1}],'metadata':{'continue':'next-page'}},{'items':[{'id':2}]},{'items':[]}]
        responses=[]
        for page in pages:
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            response.read.return_value=json.dumps(page).encode();responses.append(response)
        with patch.object(sdk.InventorySdk,'__call__',return_value=details),patch('ssl.create_default_context') as tls,patch('urllib.request.urlopen',side_effect=responses) as http:
            result=transport.kubernetes_storage('us-east-1','test-cluster')
            self.assertEqual(result['pvs'],[{'id':1},{'id':2}]);self.assertEqual(result['pvcs'],[])
            tls.assert_called_once_with(cadata='FAKE-CA')
            for call in http.call_args_list:
                request=call.args[0];self.assertEqual(request.get_method(),'GET')
                self.assertTrue(request.full_url.startswith('https://example.eks.amazonaws.com/api/v1/persistent'))
                token=request.get_header('Authorization').split('k8s-aws-v1.')[1]
                signed=base64.urlsafe_b64decode(token+'='*((-len(token))%4)).decode()
                query=parse_qs(urlsplit(signed).query)
                self.assertIn('x-k8s-aws-id',query['X-Amz-SignedHeaders'][0])
                self.assertEqual(query['X-Amz-Expires'],['60'])
            self.assertIn('continue=next-page',http.call_args_list[1].args[0].full_url)

    def test_invalid_endpoint_rejected_before_token_or_network(self):
        sdk=module('direct_sdk');transport=sdk.InventorySdk({'AccessKeyId':'ASIA-test-only','SecretAccessKey':'test','SessionToken':'test'},session_factory=Mock())
        with patch.object(sdk.InventorySdk,'__call__',return_value={'cluster':{'endpoint':'http://unsafe'}}),patch('urllib.request.urlopen') as http:
            with self.assertRaises(ValueError):transport.kubernetes_storage('us-east-1','test')
            http.assert_not_called()
