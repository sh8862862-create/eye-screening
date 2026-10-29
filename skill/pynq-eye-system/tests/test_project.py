"""Offline behavior tests; no board, credentials, camera or HID access."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project.project_root()

    def test_catalog_covers_existing_resources(self):
        rows = project.catalog(self.root)
        self.assertEqual(len(rows), 14)
        self.assertTrue(all(r['reference_exists'] and not r['missing_resources'] for r in rows))

    def test_explicit_invalid_root_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                project.project_root(directory)

    def test_environment_root_supports_detached_skill(self):
        with patch.dict(os.environ, {'EYE_PROJECT_ROOT': str(self.root)}):
            self.assertEqual(project.project_root(), self.root)

    def test_outside_path_rejected(self):
        with self.assertRaises(ValueError):
            project.confined(self.root, '../outside.txt')

    def test_locate_checks_archive_and_rejects_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            full = Path(directory)
            (full / 'report/organization').mkdir(parents=True)
            source = full / 'original.py'
            source.write_text('x = 1\n')
            inventory = {'files': [{'root': 'workspace', 'original': 'train.py',
                                   'path': 'original.py', 'sha256': project.sha(source)}]}
            (full / 'report/organization/file_inventory.json').write_text(json.dumps(inventory))
            with patch.dict(os.environ, {'EYE_FULL_PACKAGE_ROOT': str(full)}):
                self.assertEqual(project.resolve_original(self.root, 'train.py'), source)
                source.write_text('changed')
                with self.assertRaises(ValueError):
                    project.resolve_original(self.root, 'train.py')
                with self.assertRaises(ValueError):
                    project.resolve_original(self.root, 'missing.py')

    def test_deploy_uses_final_worker_and_tracker(self):
        plan = project.read_json(project.SKILL / 'assets/deployment_map.json')
        targets = {r['target']: r['source'] for r in plan['files']}
        self.assertEqual(targets['/home/xilinx/gesture_demo/camera_web/workers.py'], 'src/ps/camera_web/workers.py')
        self.assertEqual(targets['/home/xilinx/gesture_demo/gesture_image_test/hand_tracker.py'], 'src/ps/gesture/hand_tracker.py')
        self.assertEqual(len(targets), len(plan['files']))
        self.assertFalse(any('/data/' in t for t in targets))

    def test_release_archive_content_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'assets').mkdir()
            (root / 'resources').mkdir()
            (root / 'source.py').write_text('print(1)\n')
            row = {'source': 'source.py', 'target': '/opt/test/source.py', 'mode': '0640'}
            (root / 'assets/deployment_map.json').write_text(json.dumps(
                {'files': [row], 'preconditions': [], 'preserve': [], 'postchecks': []}))
            (root / 'assets/resource_manifest.json').write_text(json.dumps(
                {'files': [{'path': 'source.py', 'sha256': project.sha(root / 'source.py')}]}))
            (root / 'resources/manifest.json').write_text(json.dumps({'files': []}))
            destination = root / 'release.tgz'
            with patch.object(project, 'SKILL', root):
                result = project.pack_release(root, destination)
            self.assertFalse(result['installed'])
            with tarfile.open(destination) as archive:
                plan = json.load(archive.extractfile('deployment_manifest.json'))
                for entry in plan['files']:
                    member = archive.getmember('rootfs/' + entry['target'].lstrip('/'))
                    self.assertEqual(member.mode, int(entry['mode'], 8))
                    self.assertEqual((member.uid, member.gid), (0, 0))
                    data = archive.extractfile(member).read()
                    self.assertEqual(project.hashlib.sha256(data).hexdigest(), entry['sha256'])
            with patch.object(project, 'SKILL', root), self.assertRaises(ValueError):
                project.pack_release(root, destination)

    def test_changed_source_rejected_before_packaging(self):
        with patch.object(project, 'sha', return_value='unexpected'):
            with self.assertRaises(ValueError):
                project.release_plan(self.root)

    def test_read_only_snapshot_requests_only_two_endpoints(self):
        calls = []
        def response(url, timeout):
            calls.append(url)
            return io.BytesIO(b'{"phase":"IDLE"}')
        with patch.object(project, 'urlopen', side_effect=response):
            snapshot = project.collect_snapshot('http://example.test:8080')
        self.assertEqual(calls, ['http://example.test:8080/api/state', 'http://example.test:8080/api/devices'])
        self.assertTrue(snapshot['read_only'])
        self.assertFalse(snapshot['errors'])

    def test_connection_failure_is_not_reported_as_healthy(self):
        with patch.object(project, 'urlopen', side_effect=TimeoutError('timeout')):
            snapshot = project.collect_snapshot('http://example.test')
        self.assertEqual(set(snapshot['errors']), {'state', 'devices'})
        self.assertGreaterEqual(len(project.diagnose(snapshot)['findings']), 2)

    def test_credentials_and_non_http_urls_rejected(self):
        for url in ('http://user:password@example.test', 'file:///etc/passwd', 'http://example.test/api/state'):
            with self.assertRaises(ValueError):
                project.collect_snapshot(url)

    def test_stale_camera_and_result_are_separate_findings(self):
        findings = project.diagnose({'phase': 'RUNNING', 'camera_active': True, 'frame_fresh': False,
                                     'inference_active': True, 'result_stale': True})
        self.assertEqual(len(findings['findings']), 2)
        self.assertEqual(findings['mutations'], 0)

    def test_build_defaults_to_plan_without_execution(self):
        with patch.object(project, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(project.main(['build', 'hls']), 0)
            run.assert_not_called()

    def test_infer_passes_current_metadata_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'image.jpg'
            image.write_bytes(b'fixture; inference mocked')
            output = Path(directory) / 'result'
            with patch.object(project, 'run', return_value=7) as run, patch.object(project, 'require_resources'):
                code = project.main(['infer', '--image', str(image), '--output-dir', str(output), '--localize'])
            self.assertEqual(code, 7)
            command = [str(v) for v in run.call_args.args[0]]
            self.assertTrue(command[command.index('--metadata') + 1].endswith('mobilenet_v3_small.json'))
            self.assertIn('--localizer', command)
            with self.assertRaises(ValueError):
                project.main(['infer', '--image', str(image), '--output-dir', str(output)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
