"""Read-only SHA256/size verification for a list of {path, bytes, sha256}."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(root, entries):
    root = Path(root).resolve()
    errors = []
    for entry in entries:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            errors.append({'path': entry['path'], 'error': 'missing or outside root'})
            continue
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != entry['sha256'] or path.stat().st_size != entry['bytes']:
            errors.append({'path': entry['path'], 'error': 'content mismatch'})
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    rows = manifest if isinstance(manifest, list) else manifest['files']
    errors = verify(args.root, rows)
    print(json.dumps({'checked': len(rows), 'errors': errors}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(errors))


if __name__ == '__main__':
    main()
