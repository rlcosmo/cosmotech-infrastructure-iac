"""Build only the reviewed runtime; no workspace files/secrets in the archive."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
ROOT = Path(__file__).resolve().parents[1]

def main():
    output = ROOT / 'build/power-manager.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in ('__init__.py', 'api.py', 'core.py', 'inventory.py', 'costs.py', 'executor.py','compute-rates.json'):
            info = ZipInfo('power_manager/' + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (ROOT / 'power_manager' / name).read_bytes())
    print('Built Power Manager runtime (no credentials or local metadata)')

if __name__ == '__main__':
    main()
