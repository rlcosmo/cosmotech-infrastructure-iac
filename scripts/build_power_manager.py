"""Build only the reviewed runtime; no workspace files/secrets in the archive."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
ROOT = Path(__file__).resolve().parents[1]

def main():
    output = ROOT / 'build/power-manager.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in ('__init__.py', 'api.py', 'core.py', 'inventory.py', 'costs.py'):
            archive.write(ROOT / 'power_manager' / name, 'power_manager/' + name)
    print('Built Power Manager runtime (no credentials or local metadata)')

if __name__ == '__main__':
    main()
