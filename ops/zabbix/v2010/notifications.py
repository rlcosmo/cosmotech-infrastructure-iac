"""Small authenticated incident projection; stores no event text or payload bodies."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import time

FIELDS={'event_id','state','service','severity','occurred_at'}


def validate(data, now=None):
    if not isinstance(data,dict) or set(data)!=FIELDS:raise ValueError('schema')
    if not isinstance(data['event_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',data['event_id']):raise ValueError('id')
    if data['state'] not in ('problem','recovery'):raise ValueError('state')
    if not isinstance(data['service'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',data['service']):raise ValueError('service')
    if type(data['severity']) is not int or not 0<=data['severity']<=5:raise ValueError('severity')
    if type(data['occurred_at']) is not int or not (now or time.time())-604800<=data['occurred_at']<=(now or time.time())+300:raise ValueError('time')
    return data


class Store:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS events(event_id TEXT,state TEXT,service TEXT,severity INTEGER,occurred_at INTEGER,received_at INTEGER,PRIMARY KEY(event_id,state))')
    def connect(self):return sqlite3.connect(self.path,timeout=10)
    def record(self,data,now=None):
        now=int(now or time.time());validate(data,now)
        with self.connect() as db:
            existing=db.execute('SELECT service FROM events WHERE event_id=? LIMIT 1',(data['event_id'],)).fetchone()
            if existing and existing[0]!=data['service']:raise ValueError('conflicting service')
            db.execute('DELETE FROM events WHERE received_at<?',(now-604800,))
            cursor=db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',tuple(data[k] for k in ['event_id','state','service','severity','occurred_at'])+(now,))
            return bool(cursor.rowcount)
    def incidents(self):
        with self.connect() as db:
            rows=db.execute("SELECT event_id,service,MAX(severity),MIN(CASE WHEN state='problem' THEN occurred_at END),MAX(CASE WHEN state='recovery' THEN occurred_at END),MAX(received_at) FROM events GROUP BY event_id,service ORDER BY MAX(received_at) DESC LIMIT 200").fetchall()
        return [dict(zip(['event_id','service','severity','opened_at','recovered_at','received_at'],r),state='recovery' if r[4] is not None else 'problem') for r in rows]


def handler(store,credentials,page):
    reader='Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()
    ingest='Bearer '+credentials['ingest_token']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,status,value,kind='application/json'):
            body=value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
            self.send_response(status)
            for k,v in {'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"}.items():self.send_header(k,v)
            if status==401:self.send_header('WWW-Authenticate','Basic realm="CosmoTech lab notifications"')
            self.end_headers();self.wfile.write(body)
        def auth(self,expected):return hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode())
        def do_GET(self):
            if self.path=='/health':return self.respond(200,{'ok':True})
            if not self.auth(reader):return self.respond(401,{'error':'unauthorized'})
            if self.path in ('/','/index.html'):return self.respond(200,page.read_bytes(),'text/html; charset=utf-8')
            if self.path=='/events':return self.respond(200,{'incidents':store.incidents()})
            return self.respond(404,{'error':'not_found'})
        def do_POST(self):
            if self.path!='/events':return self.respond(404,{'error':'not_found'})
            if not self.auth(ingest):return self.respond(401,{'error':'unauthorized'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16384:return self.respond(413,{'error':'size'})
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'content_type'})
                data=json.loads(self.rfile.read(length));created=store.record(data)
            except (ValueError,TypeError,UnicodeError):return self.respond(400,{'error':'invalid_event'})
            return self.respond(202,{'accepted':True,'new':created})
    return Handler


if __name__=='__main__':
    credentials=json.loads(Path('/run/secrets/notifications').read_text())
    if any(not isinstance(credentials.get(k),str) or len(credentials[k])<16 for k in ['password','ingest_token']):raise SystemExit('Invalid runtime credentials')
    ThreadingHTTPServer(('0.0.0.0',8094),handler(Store('/data/events.sqlite'),credentials,Path(__file__).with_name('index.html'))).serve_forever()
