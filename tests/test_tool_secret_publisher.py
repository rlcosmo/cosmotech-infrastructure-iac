"""Ensure credential writes remain bounded and retries do not create duplicate versions."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).parents[1]/'scripts/publish_tool_secrets.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class SecretPublisherTests(unittest.TestCase):
    def test_rejects_non_catalogued_secret_before_any_aws_call(self):
        call = Mock()
        with self.assertRaises(ValueError):
            publisher.publish({'other/secret': {'password': 'test'}}, {'grafana': 'cosmotech/grafana/credentials'}, call)
        call.assert_not_called()

    def test_identical_retry_preserves_current_version(self):
        value = {'username': 'service', 'password': 'test'}
        call = Mock(return_value={'SecretString': json.dumps(value)})
        result = publisher.publish({'allowed': value}, {'x': 'allowed'}, call)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result, [{'secret_id': 'allowed', 'status': 'unchanged'}])

    def test_write_returns_metadata_without_credentials(self):
        call = Mock(side_effect=[{}, {'VersionId': 'version'}])
        value = {'username': 'service', 'password': 'do-not-log'}
        result = publisher.publish({'allowed': value}, {'x': 'allowed'}, call)
        self.assertEqual(result[0]['status'], 'stored')
        self.assertNotIn('do-not-log', str(result))
        self.assertEqual(call.call_args.kwargs['value'], value)
