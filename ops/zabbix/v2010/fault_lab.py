"""Loopback-only faults, optionally measured into one dedicated Zabbix trapper host."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import socket
import struct
import subprocess
import threading
import time
import urllib.request
import urllib.error

HOST='cosmotech-v2010-isolated-lab'
BASELINE={'http_errors':0,'latency_ms':0,'service_available':1,'agent_available':1,**{'state['+s+']':0 for s in ['http','latency','unavailable','agent']}}


def sender(values):
    data={'request':'sender data','data':[{'host':HOST,'key':'v2010.lab.'+k,'value':str(v),'clock':int(time.time())} for k,v in values.items()]}
    body=json.dumps(data).encode()
    with socket.create_connection(('127.0.0.1',10051),timeout=5) as connection:
        connection.sendall(b'ZBXD\x01'+struct.pack('<Q',len(body))+body)
        def receive(n):
            value=b''
            while len(value)<n:
                part=connection.recv(n-len(value))
                if not part:raise RuntimeError('Sender response incomplete')
                value+=part
            return value
        header=receive(13)
        if header[:5]!=b'ZBXD\x01':raise RuntimeError('Unexpected trapper protocol')
        size=struct.unpack('<Q',header[5:])[0]
        if size>65536:raise RuntimeError('Oversized sender response')
        response=json.loads(receive(size))
        if response.get('response')!='success' or 'failed: 0' not in response.get('info',''):raise RuntimeError('Dedicated trapper host did not accept samples')


def compose(*args):
    r=subprocess.run(['docker','compose',*args],cwd='/opt/cosmotech/zabbix',capture_output=True,text=True,timeout=60)
    if r.returncode:raise RuntimeError('Isolated lab container operation failed')
    return r.stdout


def scenario(name,duration,live=False):
    if live and not Path('/opt/cosmotech/zabbix/secrets/credentials.json').is_file():raise RuntimeError('Live mode only on the dedicated monitoring host')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if name=='latency':time.sleep(0.8)
            self.send_response(503 if name=='http' else 200);self.end_headers()
            try:self.wfile.write(b'isolated synthetic endpoint')
            except BrokenPipeError:pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    agent_created=False
    try:
        if live:sender(BASELINE)
        if name=='agent' and live:
            compose('--profile','v2010-lab','up','-d','--no-deps','lab-agent');agent_created=True
            for _ in range(15):
                try:
                    if '[u|1]' in compose('exec','-T','lab-agent','zabbix_agent2','-t','agent.ping'):break
                except RuntimeError:pass
                time.sleep(1)
            else:raise RuntimeError('Isolated Agent 2 did not become ready')
            compose('stop','--timeout','10','lab-agent')
            measured={'agent_available':0}
        elif name=='agent':
            probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
            try:socket.create_connection(('127.0.0.1',port),timeout=.2);raise RuntimeError('Expected closed isolated agent endpoint')
            except OSError:measured={'agent_available':0}
        elif name=='unavailable':
            port=server.server_port;server.shutdown();server.server_close();thread.join()
            try:urllib.request.urlopen(f'http://127.0.0.1:{port}',timeout=1);raise RuntimeError('Expected unavailable endpoint')
            except urllib.error.URLError:measured={'service_available':0}
        else:
            start=time.monotonic()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}',timeout=3) as r:code=r.status
            except urllib.error.HTTPError as error:code=error.code
            measured={'http_errors':1} if name=='http' and code==503 else {'latency_ms':round((time.monotonic()-start)*1000)}
            if name=='latency' and measured['latency_ms']<700:raise RuntimeError('Latency fault did not occur')
        if live:sender({**measured,'state['+name+']':1})
        print(json.dumps({'scenario':name,'measured':measured,'live':live,'duration_seconds':duration}),flush=True)
        time.sleep(duration)
    finally:
        failures=[];restored=not live
        try:
            if agent_created:compose('start','lab-agent')
        except RuntimeError:failures.append('lab_agent_restart')
        try:
            if live:sender(BASELINE)
            restored=True
        except (RuntimeError,OSError):failures.append('baseline_delivery')
        server.shutdown();server.server_close();thread.join(timeout=3)
        try:
            if agent_created:compose('rm','--stop','--force','lab-agent')
        except RuntimeError:failures.append('lab_agent_cleanup')
        print(json.dumps({'scenario':name,'cleanup':not failures,'baseline_restored':restored}),flush=True)
        if failures:raise RuntimeError('Lab cleanup failed: '+','.join(failures))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('scenario',choices=['all','http','latency','unavailable','agent','seed']);p.add_argument('--duration',type=int,default=5);p.add_argument('--live',action='store_true');a=p.parse_args()
    if not 1<=a.duration<=180:p.error('Duration must be between 1 and 180 seconds')
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit(143)))
    if a.scenario=='seed':
        if not a.live:raise SystemExit('Seed requires explicit --live on the monitoring host')
        sender({'discovery':json.dumps({'data':[{'{#SCENARIO}':s} for s in ['http','latency','unavailable','agent']]})})
        print(json.dumps({'discovery_seed_sent':True}))
    else:
        for name in ['http','latency','unavailable','agent'] if a.scenario=='all' else [a.scenario]:scenario(name,a.duration,a.live)
