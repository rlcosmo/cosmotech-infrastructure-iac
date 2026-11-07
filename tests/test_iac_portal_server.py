import base64
import hashlib
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from src.iac_portal.server import handler


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.page = self.directory / "index.html"
        self.page.write_text("<title>Portal</title>")
        self.secret = b"reader:test-only-credential"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(
            self.directory, self.page, hashlib.sha256(self.secret).hexdigest()))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, *, authenticated=True, method="GET"):
        connection = HTTPConnection(*self.server.server_address)
        headers = {"Authorization": "Basic " + base64.b64encode(self.secret).decode()} if authenticated else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read(), response.getheader("Cache-Control")
        connection.close()
        return result

    def test_authentication_and_no_write_routes(self):
        self.assertEqual(self.request("/", authenticated=False)[0], 401)
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/api/history", method="POST")[0], 501)
        self.assertEqual(self.request("/../../terraform.tfstate")[0], 404)

    def test_history_and_missing_or_corrupt_evidence(self):
        self.assertEqual(json.loads(self.request("/api/history")[1]), [])
        (self.directory / "report-001.json").write_text('{"schema_version":1}')
        status, body, cache = self.request("/api/history")
        self.assertEqual((status, cache), (200, "no-store"))
        self.assertEqual(len(json.loads(body)), 1)
        (self.directory / "report-002.json").write_text("corrupt")
        self.assertEqual(self.request("/api/history")[0], 503)

    def test_missing_credential_fails_closed(self):
        with self.assertRaises(ValueError):
            handler(self.directory, self.page, "")

    def test_direct_comparison_is_authenticated_and_refresh_only_queues_work(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('direct_server',Path(__file__).parents[1]/'ops/iac-portal/portal_server.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        comparison=self.directory/'comparison';comparison.mkdir()
        queue=self.directory/'queue';queue.mkdir()
        (comparison/'current.json').write_text(json.dumps({'schema_version':2,'state':'checking','resources':[],'source':{'commit':'a'*40}}))
        self.server.RequestHandlerClass=module.handler(self.directory,self.page,hashlib.sha256(self.secret).hexdigest(),comparison,queue)
        self.assertEqual(self.request('/api/comparison',authenticated=False)[0],401)
        status,body,_=self.request('/api/comparison')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['state'],'checking')
        self.assertEqual(self.request('/api/refresh',authenticated=False,method='POST')[0],401)
        self.assertEqual(self.request('/api/refresh',method='POST')[0],202)
        self.assertTrue((queue/'refresh.request').exists())
        self.assertEqual(self.request('/api/refresh',method='POST')[0],202)
