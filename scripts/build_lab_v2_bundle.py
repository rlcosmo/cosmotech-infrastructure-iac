"""Build deterministic code-only SSM payload; never package runtime credentials."""
import gzip
import io
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
source = root / 'ops/iac-portal'
files = ('publication_runtime.py', 'portal_server.py', 'index.html', 'v2-sqs-workers.json',
         'lab_monitoring.py', 'sqs_canary_metrics.py')
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w') as archive:
    for name in files:
        content = (source / name).read_bytes()
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = len(content), 0o644, 0
        archive.addfile(info, io.BytesIO(content))
result = gzip.compress(buffer.getvalue(), mtime=0)
if len(result) * 4 / 3 + 4096 >= 65536:
    raise SystemExit('SSM document payload exceeds the safe content budget')
(root / 'dist').mkdir(exist_ok=True)
(root / 'dist/lab-v2-publication.tgz').write_bytes(result)
print('Code-only Lab V2 bundle built')
