"""Restore archived directory names with current edition contents into a NEW directory; never deploy."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def confined(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError(f'Path outside destination: {relative}')
    return target


def main():
    package = Path(__file__).resolve().parents[2]
    inventory = json.loads((package / 'report/organization/file_inventory.json').read_text(encoding='utf-8'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', choices=sorted(inventory['roots']), default='workspace')
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        parser.error('Destination must not exist; existing files are never overwritten.')
    if destination.is_relative_to(package):
        parser.error('Choose a destination outside this package.')
    rows = [r for r in inventory['files'] if r['root'] == args.root]
    for row in rows:
        source = confined(package, row['path'])
        confined(destination, row['original'])
        if sha256(source) != row['sha256']:
            raise ValueError(f'Archive checksum mismatch: {row["path"]}')
    destination.mkdir(parents=True)
    for entry in inventory['directories']:
        if entry['root'] == args.root:
            confined(destination, entry['relative']).mkdir(parents=True, exist_ok=True)
    for row in rows:
        source = confined(package, row['path'])
        target = confined(destination, row['original'])
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if sha256(target) != row['sha256']:
            raise ValueError(f'Restored checksum mismatch: {target}')
    print(json.dumps({'root': args.root, 'files': len(rows), 'destination': str(destination), 'verified': True}))


if __name__ == '__main__':
    main()
