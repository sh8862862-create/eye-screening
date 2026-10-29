"""Verify package inventory, current sources, filenames and optional originals."""
import argparse
import json
from pathlib import Path
import re
import sys


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'skill/artifact_integrity'))
sys.dont_write_bytecode = True
from verify_manifest import verify

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check-originals', action='store_true', help='Only on the author workstation; verify archived source trees are unchanged.')
args = parser.parse_args()
inventory = json.loads((root / 'report/organization/file_inventory.json').read_text(encoding='utf-8'))
current = json.loads((root / 'report/organization/current_sources.json').read_text(encoding='utf-8'))
errors = verify(root, inventory['files']) + verify(root, current)
expected = {'src', 'sim', 'build', 'board', 'data', 'skill', 'report'}
if {p.name for p in root.iterdir() if p.is_dir()} != expected:
    errors.append({'error': 'unexpected top-level directories'})
bad_names = [str(p.relative_to(root)) for p in root.rglob('*')
             if p.name not in ('README.md', 'SKILL.md') and not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]*', p.name)]
errors.extend({'path': p, 'error': 'nonconforming name'} for p in bad_names)
if args.check_originals:
    for key, origin in inventory['roots'].items():
        rows = [{**r, 'path': r['original'], 'sha256': r.get('source_sha256', r['sha256']), 'bytes': r.get('source_bytes', r['bytes'])} for r in inventory['files'] if r['root'] == key]
        errors.extend(verify(Path(origin), rows))
result = {'archive_files': len(inventory['files']), 'current_files': len(current),
          'originals_checked': args.check_originals, 'errors': errors}
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(bool(errors))
