"""Verify a source checkout; model binaries and historical archives are optional."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import resources

def verify(root=ROOT):
    errors = []
    spec = resources.manifest(root)
    external = {r['path'] for r in spec['files']}
    required = json.loads((root / 'resources/repository_files.json').read_text(encoding='utf-8'))
    for relative in required:
        if not (root / relative).is_file():
            errors.append('Missing repository file: ' + relative)
    for p in root.rglob('*.py'):
        if any(part in {'.git', '.venv', '__pycache__', 'work', 'archive'} for part in p.relative_to(root).parts):
            continue
        try:
            ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p))
        except (SyntaxError, UnicodeError) as error:
            errors.append(str(error))
    for p in root.rglob('*.md'):
        if any(part in {'.git', '.venv', 'work'} for part in p.relative_to(root).parts):
            continue
        for link in re.findall(r'\]\(([^)]+)\)', p.read_text(encoding='utf-8')):
            if '://' in link or link.startswith('#'):
                continue
            if not (p.parent / link.split('#')[0]).exists():
                errors.append('Broken link: ' + str(p.relative_to(root)) + ' -> ' + link)
    skill = root / 'skill/pynq-eye-system'
    body = (skill / 'SKILL.md').read_text(encoding='utf-8')
    if not re.match(r'^---\nname: pynq-eye-system\ndescription: .+\n---', body):
        errors.append('Invalid skill metadata')
    for row in json.loads((skill / 'assets/capabilities.json').read_text(encoding='utf-8'))['capabilities']:
        if not (skill / row['reference']).is_file():
            errors.append('Missing skill reference: ' + row['reference'])
        for name in row['resources']:
            if not (root / name).is_file() and name not in external:
                errors.append('Missing capability source: ' + name)
    state = resources.status(root)
    errors += ['Changed external resource: ' + x for x in state['mismatched']]
    if (root / '.git').exists():
        proc = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True)
        for name in proc.stdout.decode('utf-8').split('\0'):
            if not name:
                continue
            p = root / name
            if name in external or '/archive/' in '/' + name or p.suffix in {'.pyc', '.log'}:
                errors.append('Excluded artifact is tracked: ' + name)
            if p.is_file() and p.stat().st_size >= 50 * 1024 * 1024:
                errors.append('Oversized Git file: ' + name)
    return {'mode': 'source_repository', 'required_files': len(required),
            'optional_resources_missing': len(state['missing']), 'errors': errors,
            'hardware_validated': False}

if __name__ == '__main__':
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['errors']))
