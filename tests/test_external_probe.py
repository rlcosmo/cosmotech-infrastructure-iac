"""External checks stay read-only, bounded and report failed public paths."""
import importlib.util
import json
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('external_probe', Path(__file__).parents[1]/'src/synthetic_probe/lambda_function.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ExternalProbeTests(unittest.TestCase):
    def response(self, status=200):
        value = Mock(status=status)
        value.read.return_value = b'private-response-not-to-log'
        value.__enter__ = Mock(return_value=value)
        value.__exit__ = Mock(return_value=False)
        return value

    def test_fixed_hosts_ignore_event_supplied_urls_and_emit_correlated_logs(self):
        client = Mock()
        client.open.return_value = self.response()
        with patch.object(probe.urllib.request, 'build_opener', return_value=client), patch('builtins.print') as output:
            result = probe.handler({'url': 'http://169.254.169.254/'}, None)
        self.assertEqual(result['successful_checks'], 2)
        for call in client.open.call_args_list:
            request = call.args[0]
            self.assertTrue(request.full_url.startswith('https://orders-'))
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(call.kwargs['timeout'], 5)
            self.assertTrue(request.get_header('Traceparent').startswith('00-'))
        self.assertNotIn('private-response-not-to-log', str(output.call_args_list))

    def test_failure_preserves_second_check_and_raises_for_alerting(self):
        client = Mock()
        client.open.side_effect = [urllib.error.HTTPError('https://fixed/', 503, 'down', {}, BytesIO(b'secret')), self.response()]
        with patch.object(probe.urllib.request, 'build_opener', return_value=client), patch('builtins.print') as output, self.assertRaises(RuntimeError):
            probe.handler({}, None)
        self.assertEqual(client.open.call_count, 2)
        self.assertEqual(json.loads(output.call_args_list[0].args[0])['status_code'], 503)
        self.assertNotIn('secret', str(output.call_args_list))
