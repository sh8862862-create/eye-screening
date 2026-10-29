"""Exercise resource import with tiny fixtures, never production data."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import resources

class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        (self.root / 'resources').mkdir(parents=True)
        self.archive = Path(self.temp.name) / 'resources.zip'
        self.rows = []

    def fixture(self, entries=None):
        entries = entries or {'data/models/a.bin': b'aaa', 'board/release/b.bit': b'bbb'}
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, data in entries.items():
                z.writestr(name, data)
        self.rows = [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                     for name, data in entries.items()]
        self.spec = {'files': self.rows, 'archive_sha256': resources.digest(self.archive)}
        self.save()

    def save(self):
        (self.root / 'resources/manifest.json').write_text(json.dumps(self.spec), encoding='utf-8')

    def test_absent_resources_are_visible(self):
        self.fixture()
        self.assertEqual(len(resources.status(self.root)['missing']), 2)

    def test_roundtrip_and_idempotence(self):
        self.fixture()
        for _ in range(2):
            result = resources.import_archive(self.root, self.archive)
            self.assertEqual(len(result['present']), 2)
            self.assertFalse(result['missing'] or result['mismatched'])

    def test_wrong_archive_hash_writes_nothing(self):
        self.fixture()
        self.spec['archive_sha256'] = 'wrong'
        self.save()
        with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)
        self.assertFalse((self.root / 'data').exists())

    def test_member_hash_failure_is_preflight(self):
        self.fixture()
        self.spec['files'][1]['sha256'] = 'wrong'
        self.save()
        with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)
        self.assertFalse((self.root / 'data').exists())

    def test_existing_changed_resource_is_preserved(self):
        self.fixture()
        target = self.root / self.rows[1]['path']
        target.parent.mkdir(parents=True)
        target.write_bytes(b'local changes')
        with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)
        self.assertEqual(target.read_bytes(), b'local changes')
        self.assertFalse((self.root / 'data').exists())

    def test_traversal_and_windows_paths_rejected(self):
        for name in ['../escape.bin', '/absolute.bin', 'C:/escape.bin', 'a\\b.bin']:
            self.fixture({name: b'bad'})
            with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)

    def test_extra_and_duplicate_entries_rejected(self):
        self.fixture()
        with zipfile.ZipFile(self.archive, 'a') as z: z.writestr('extra.bin', b'extra')
        self.spec['archive_sha256'] = resources.digest(self.archive)
        self.save()
        with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)
        self.assertFalse((self.root / 'data').exists())
        self.fixture()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.archive, 'a') as z:
                z.writestr(self.rows[0]['path'], b'aaa')
        self.spec['archive_sha256'] = resources.digest(self.archive)
        self.save()
        with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)

    def test_modified_import_is_reported(self):
        self.fixture()
        resources.import_archive(self.root, self.archive)
        (self.root / self.rows[0]['path']).write_bytes(b'changed')
        self.assertEqual(resources.status(self.root)['mismatched'], [self.rows[0]['path']])

    def test_symlink_entry_rejected(self):
        self.fixture({'link.bin': b'target'})
        with zipfile.ZipFile(self.archive, 'w') as z:
            info = zipfile.ZipInfo('link.bin')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, b'target')
        self.spec['archive_sha256'] = resources.digest(self.archive)
        self.save()
        with self.assertRaises(ValueError): resources.import_archive(self.root, self.archive)

    def test_partial_write_failure_rolls_back_new_files(self):
        self.fixture()
        original = resources.shutil.copyfileobj
        calls = [0]
        def copy(source, destination, *args, **kwargs):
            calls[0] += 1
            if calls[0] == 4:
                destination.write(b'partial')
                raise OSError('simulated disk failure')
            return original(source, destination, *args, **kwargs)
        with patch.object(resources.shutil, 'copyfileobj', side_effect=copy), self.assertRaises(OSError):
            resources.import_archive(self.root, self.archive)
        for row in self.rows: self.assertFalse((self.root / row['path']).exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)
