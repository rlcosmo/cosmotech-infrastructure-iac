"""Create the version-matched telemetry schema without putting passwords in command arguments."""
import subprocess
import time
from pathlib import Path
from xml.sax.saxutils import escape
root=Path('/opt/cosmotech/zabbix')
password=(root/'secrets/clickhouse_password').read_text().strip()
config='<config><host>localhost</host><port>9000</port><user>zabbix</user><password>'+escape(password)+'</password></config>'
sql=(Path(__file__).parent/'telemetry-schema.sql').read_text()
script='umask 077; f=$(mktemp); cat > "$f"; clickhouse-client --config-file "$f" --multiquery --query "$1"; result=$?; rm -f "$f"; exit "$result"'
for attempt in range(30):
    result=subprocess.run(['docker','compose','exec','-T','clickhouse','sh','-c',script,'client',sql],cwd=root,input=config,text=True,capture_output=True,check=False)
    if result.returncode==0:
        print('Version-matched telemetry schema ready; retention 3 days')
        break
    time.sleep(2)
else:
    raise RuntimeError('ClickHouse telemetry schema initialization failed; inspect locally with credentials protected')
