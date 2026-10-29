"""Select repository validation, or explicitly validate an external full package."""
import argparse
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--full-package-root', type=Path)
args = parser.parse_args()
if args.full_package_root:
    full = args.full_package_root.resolve()
    script = full / 'sim/verify_package.py'
    if full == root or not script.is_file() or not (full / 'report/organization/file_inventory.json').is_file():
        parser.error('Provide a separate original full package with its original inventory and verifier')
    command = [sys.executable, '-B', str(script)]
    cwd = full
else:
    command = [sys.executable, '-B', str(root / 'sim/verify_repository.py')]
    cwd = root
raise SystemExit(subprocess.call(command, cwd=cwd))
