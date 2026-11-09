import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('notifier',ROOT/'ops/zabbix/v2010/notifications.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class NotificationTests(unittest.TestCase):
    def event(self,**kw):return {'event_id':'123','service':'isolated-lab','severity':3,'state':'problem','occurred_at':int(time.time()),**kw}
    def test_dedup_recovery_first_and_no_reopening(self):
        with tempfile.TemporaryDirectory() as d:
            s=mod.Store(Path(d)/'db');self.assertTrue(s.record(self.event(state='recovery')))
            self.assertTrue(s.record(self.event()));self.assertFalse(s.record(self.event()))
            self.assertEqual(s.incidents()[0]['state'],'recovery')
            with self.assertRaises(ValueError):s.record(self.event(service='other-service'))
    def test_sensitive_extra_data_and_injection_rejected(self):
        for changes in [{'password':'must-not-store'},{'service':'<script>'},{'severity':True},{'occurred_at':0}]:
            with self.assertRaises(ValueError):mod.validate(self.event(**changes))
    def test_authentication_and_real_http_contract(self):
        with tempfile.TemporaryDirectory() as d:
            s=mod.Store(Path(d)/'db');credentials={'username':'reader','password':'test-password-only','ingest_token':'test-ingest-only'}
            server=mod.ThreadingHTTPServer(('127.0.0.1',0),mod.handler(s,credentials,ROOT/'ops/zabbix/v2010/index.html'))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                for auth in ['', 'Bearer wrong']:
                    req=urllib.request.Request(base+'/events',data=json.dumps(self.event()).encode(),headers={'Authorization':auth,'Content-Type':'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
                    self.assertEqual(error.exception.code,401)
                req=urllib.request.Request(base+'/events',data=json.dumps(self.event()).encode(),headers={'Authorization':'Bearer test-ingest-only','Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:self.assertEqual(response.status,202)
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/events')
                self.assertEqual(len(s.incidents()),1)
            finally:server.shutdown();server.server_close();worker.join()
