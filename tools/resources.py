"""Verify/import the versioned resource ZIP. Never downloads or deploys."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def manifest(root):
    return json.loads((Path(root) / 'resources/manifest.json').read_text(encoding='utf-8'))

def confined(root, name):
    rel = PurePosixPath(name)
    if not name or rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name:
        raise ValueError('Invalid resource path: ' + name)
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()) or path == Path(root).resolve():
        raise ValueError('Resource escapes project: ' + name)
    return path

def status(root):
    result = {'present': [], 'missing': [], 'mismatched': []}
    for row in manifest(root)['files']:
        path = confined(root, row['path'])
        key = 'missing' if not path.is_file() else 'present' if path.stat().st_size == row['bytes'] and digest(path) == row['sha256'] else 'mismatched'
        result[key].append(row['path'])
    return result

def import_archive(root, archive):
    root, archive = Path(root).resolve(), Path(archive).resolve()
    spec = manifest(root)
    if digest(archive) != spec['archive_sha256']:
        raise ValueError('Resource archive SHA256 differs from manifest')
    rows = spec['files']
    expected = {r['path']: r for r in rows}
    if len(expected) != len(rows):
        raise ValueError('Duplicate manifest path')
    # Validate all files and destinations before writing anything into the repository.
    with tempfile.TemporaryDirectory(prefix='eye-resources-') as temp:
        staged = Path(temp)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise ValueError('Unexpected, missing or duplicate resource ZIP entries')
            for name, row in expected.items():
                target = confined(root, name)
                stage = confined(staged, name)
                info = z.getinfo(name)
                if info.is_dir() or info.file_size != row['bytes'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Invalid resource entry: ' + name)
                if target.exists() and (not target.is_file() or digest(target) != row['sha256']):
                    raise ValueError('Refuse to overwrite modified resource: ' + name)
                stage.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as source, stage.open('xb') as output:
                    shutil.copyfileobj(source, output)
                if digest(stage) != row['sha256']:
                    raise ValueError('Resource checksum mismatch: ' + name)
        installed = []
        try:
            for name in expected:
                target = confined(root, name)
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    installed.append(target)
                    with (staged / name).open('rb') as source:
                        shutil.copyfileobj(source, output)
        except BaseException:
            for target in installed:
                target.unlink()
            raise
    return status(root)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('verify')
    command = sub.add_parser('import')
    command.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    result = import_archive(args.project_root, args.archive) if args.command == 'import' else status(args.project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result['mismatched'] or (args.command != 'status' and result['missing'])))

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print('Resource operation failed: ' + str(error))
        raise SystemExit(2)
