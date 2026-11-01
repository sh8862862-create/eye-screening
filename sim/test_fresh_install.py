"""Check install transactions in temporary rootfs directories; never touch a board."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'board'))
import install_fresh
import configure_cameras


class FreshInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'rootfs'
        self.config = Path(self.tmp.name)/'roles.json'
        self.template = ROOT/'board/config/camera_roles.example.json'

    def roles(self):
        configure_cameras.configure(self.template, self.config,
            '/dev/v4l/by-path/eye-video-index0', '/dev/v4l/by-path/gesture-video-index0', True)

    def test_confirmation_and_distinct_cameras_required(self):
        with self.assertRaises(ValueError):
            configure_cameras.configure(self.template, self.config, '/dev/video0', '/dev/video0', True)
        with self.assertRaises(ValueError):
            configure_cameras.configure(self.template, self.config, '/dev/v4l/by-path/a', '/dev/v4l/by-path/b')
        self.assertFalse(self.config.exists())

    def test_plan_is_read_only_and_complete_install_matches_hashes(self):
        self.roles()
        rows = install_fresh.plan(self.root, self.config)
        self.assertFalse(self.root.exists())
        install_fresh.install(self.root, rows)
        for row in rows:
            self.assertEqual(install_fresh.project.sha(Path(row['destination'])), row['sha256'])
        self.assertTrue(all(r['already_matches'] for r in install_fresh.plan(self.root, self.config)))
        expected = self.root/'home/xilinx/gesture_demo/camera_switch/sequential_camera.py'
        self.assertTrue(expected.is_file())
        self.assertTrue((self.root/'etc/systemd/system/pynq-camera-web.service').is_file())

    def test_conflict_rejected_before_any_other_file_written(self):
        self.roles()
        first = self.root/'opt/pynq-shared/shared_fpga.py'
        first.parent.mkdir(parents=True)
        first.write_text('existing deployment', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Existing files differ'):
            install_fresh.plan(self.root, self.config)
        self.assertFalse((self.root/'home').exists())
        self.assertEqual(first.read_text(), 'existing deployment')

    def test_destination_escape_rejected(self):
        for path in ['/opt/../../escape', '/tmp/elsewhere', 'relative']:
            with self.assertRaises(ValueError):
                install_fresh.destination(self.root, path)

    def test_source_failure_rolls_back_only_new_files(self):
        self.roles()
        rows = install_fresh.plan(self.root, self.config)
        rows[1]['sha256'] = 'not the digest'
        with self.assertRaises(ValueError):
            install_fresh.install(self.root, rows)
        self.assertFalse(Path(rows[0]['destination']).exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
