"""Offline detection, workflow gate, and service-state regression tests."""
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(os.environ.get('EYE_PRESENCE_FIXTURES', ROOT/'sim/eye_presence/fixtures'))
sys.path.insert(0, str(ROOT / 'src/ps/camera_web'))
from eye_presence import detect_eye, validate_saved_eye, NO_EYE_MESSAGE, isolated_environment
from workers import workflow_worker


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = Path(self.temp.name)
        self.image = self.session / 'image.png'
        cv2.imwrite(str(self.image), np.full((240, 320, 3), 100, np.uint8))
        self.record = dict(state='AWAITING_IMAGE_REVIEW', image=str(self.image),
                           image_sha256=hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.save(self.record)
        self.workflow = types.ModuleType('staged_workflow')
        self.workflow.load_json = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
        self.workflow.persist = self.persist
        self.workflow.verify_image = self.verify_image
        self.workflow.capture_eye = Mock(return_value=dict(state='AWAITING_IMAGE_REVIEW'))
        self.workflow.analyze_eye = Mock(return_value=dict(state='RESULT_READY'))
        context = patch.dict(sys.modules, staged_workflow=self.workflow)
        context.start()
        self.addCleanup(context.stop)

    def save(self, record):
        (self.session/'session.json').write_text(json.dumps(record), encoding='utf-8')

    def persist(self, session, record, state):
        record['state'] = state
        self.save(record)

    def verify_image(self, record):
        if hashlib.sha256(Path(record['image']).read_bytes()).hexdigest() != record['image_sha256']:
            raise ValueError('Captured image changed')
        return Path(record['image'])

    def test_blank_black_white_and_noise_rejected(self):
        images = [np.full((240, 320, 3), v, np.uint8) for v in (0, 100, 255)]
        images.append(np.random.default_rng(41).integers(0, 256, (240, 320, 3), dtype=np.uint8))
        for im in images:
            cv2.imwrite(str(self.image), im)
            result = detect_eye(self.image)
            self.assertEqual(result['status'], 'no_eye')
            self.assertFalse(result['detected'])
            self.assertEqual(result['message'], NO_EYE_MESSAGE)

    @unittest.skipUnless(list(FIXTURES.glob('eye_*.png')), "Private fixtures absent; set EYE_PRESENCE_FIXTURES")
    def test_existing_eye_images_pass(self):
        fixtures = sorted((FIXTURES).glob('eye_*.png'))
        self.assertEqual(len(fixtures), 8)
        for image in fixtures:
            with self.subTest(image=image.name):
                self.assertTrue(detect_eye(image)['detected'])

    @unittest.skipUnless(list(FIXTURES.glob('background_*.png')), "Private fixtures absent; set EYE_PRESENCE_FIXTURES")
    def test_room_crops_rejected(self):
        for image in (FIXTURES).glob('background_*.png'):
            with self.subTest(image=image.name):
                self.assertFalse(detect_eye(image)['detected'])

    @unittest.skipUnless(list(FIXTURES.glob('eye_07.png')), "Private fixtures absent; set EYE_PRESENCE_FIXTURES")
    def test_rotated_eye_passes(self):
        eye = cv2.imread(str(FIXTURES/'eye_07.png'))
        for turns in range(4):
            cv2.imwrite(str(self.image), np.rot90(eye, turns).copy())
            self.assertTrue(detect_eye(self.image)['detected'])

    def test_bad_image_is_technical_error(self):
        self.image.write_bytes(b'not an image')
        with self.assertRaisesRegex(ValueError, '无法解码'):
            detect_eye(self.image)

    def test_missing_model_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, '模型缺失'):
            detect_eye(self.image, self.session)

    def test_missing_component_fails_closed(self):
        with patch('eye_presence.cv2', types.SimpleNamespace()):
            with self.assertRaisesRegex(RuntimeError, '组件不可用'):
                detect_eye(self.image, allow_isolated=False)

    def test_minimal_runtime_uses_separate_full_runtime(self):
        with patch('eye_presence.cv2', types.SimpleNamespace()):
            result = detect_eye(self.image)
        self.assertEqual(result['status'], 'no_eye')
        self.assertTrue(result['runtime']['isolated'])

    def test_isolated_environment_preserves_unrelated_settings(self):
        original = dict(PYTHONPATH=os.pathsep.join(['/opt/opencv-neon/lib/python3.10/site-packages', '/opt/other']),
                        LD_LIBRARY_PATH=os.pathsep.join(['/opt/opencv-neon/lib', '/opt/xrt']),
                        PYNQ_PALM_PL='1')
        with patch.dict(os.environ, original):
            env = isolated_environment()
            self.assertEqual(env['PYTHONPATH'], '/opt/other')
            self.assertEqual(env['LD_LIBRARY_PATH'], '/opt/xrt')
            self.assertEqual(env['PYNQ_PALM_PL'], '1')
            self.assertEqual(os.environ['PYTHONPATH'], original['PYTHONPATH'])

    def test_isolated_invalid_result_fails_closed(self):
        with patch('eye_presence.cv2', types.SimpleNamespace()), patch('eye_presence.subprocess.run',
                return_value=types.SimpleNamespace(returncode=0, stdout='{}')):
            with self.assertRaisesRegex(RuntimeError, '无效结果'):
                detect_eye(self.image)

    def test_no_eye_does_not_invoke_classifier_or_generate_report(self):
        output = queue.Queue()
        workflow_worker(str(self.session), str(self.session), {}, 'analyze', output)
        outcome = output.get_nowait()
        self.assertTrue(outcome['ok'])
        record = outcome['record']
        self.assertEqual(record['state'], 'AWAITING_IMAGE_REVIEW')
        self.assertEqual(record['image_validation']['status'], 'no_eye')
        self.assertEqual(record['image_validation']['image_sha256'], self.record['image_sha256'])
        self.workflow.analyze_eye.assert_not_called()
        self.assertNotIn('analysis', record)
        self.assertFalse((self.session/'analysis').exists())

    def test_valid_eye_invokes_classifier_once(self):
        output = queue.Queue()
        self.record['image_sha256'] = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.save(self.record)
        # Exercise the positive workflow independently of private image assets.
        from eye_presence import validate_saved_eye as validate
        def positive(session):
            return validate(session, lambda image: dict(detected=True, status='eye_detected', message=''))
        with patch('eye_presence.validate_saved_eye', side_effect=positive):
            workflow_worker(str(self.session), str(self.session), {}, 'analyze', output)
        self.assertEqual(output.get_nowait()['record']['state'], 'RESULT_READY')
        self.workflow.analyze_eye.assert_called_once_with(self.session, True, 'cpu')
        persisted = json.loads((self.session/'session.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['image_validation']['status'], 'eye_detected')

    def test_gate_can_run_again_without_output_collision(self):
        first = validate_saved_eye(self.session)
        second = validate_saved_eye(self.session)
        self.assertEqual(first['image_validation']['status'], second['image_validation']['status'])

    def test_changed_capture_never_reaches_detector(self):
        self.image.write_bytes(b'changed')
        detect = Mock()
        with self.assertRaisesRegex(ValueError, 'changed'):
            validate_saved_eye(self.session, detect)
        detect.assert_not_called()

    def test_changed_during_detection_never_passes(self):
        def mutate(image):
            image.write_bytes(b'changed')
            return dict(detected=True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            validate_saved_eye(self.session, mutate)

    def test_ready_report_cannot_be_reanalyzed(self):
        self.record['state'] = 'RESULT_READY'
        self.save(self.record)
        with self.assertRaisesRegex(ValueError, '等待确认'):
            validate_saved_eye(self.session)

    def test_capture_path_is_unchanged(self):
        output = queue.Queue()
        workflow_worker(str(self.session), str(self.session), {}, 'capture', output)
        self.assertTrue(output.get_nowait()['ok'])
        self.workflow.capture_eye.assert_called_once()
        self.workflow.analyze_eye.assert_not_called()

    def test_service_rejection_keeps_photo_and_never_starts_gesture(self):
        from service import Manager
        root = self.session/'runtime'
        (root/'camera_switch').mkdir(parents=True)
        (root/'camera_switch/camera_roles.json').write_text(json.dumps(dict(
            roles_confirmed=True, eye='B', gesture='A')))
        manager = Manager(root, root/'data', start_thread=False)
        rejected = validate_saved_eye(self.session)
        manager.job = Mock(exitcode=0)
        manager.job.is_alive.return_value = False
        manager.job_q = Mock()
        manager.job_q.get_nowait.side_effect = [dict(ok=True, record=rejected), queue.Empty()]
        manager.busy = True
        manager.auto = False
        manager._start = Mock()
        manager._infer_on = Mock()
        manager._tick()
        snapshot = manager.snapshot()
        self.assertFalse(snapshot['report_ready'])
        self.assertFalse(snapshot['busy'])
        self.assertFalse(snapshot['auto_resume'])
        self.assertEqual(snapshot['error'], NO_EYE_MESSAGE)
        self.assertEqual(snapshot['workflow_state'], 'AWAITING_IMAGE_REVIEW')
        self.assertEqual(snapshot['image_validation']['status'], 'no_eye')
        manager._start.assert_not_called()
        manager._infer_on.assert_not_called()
        manager.record = dict(state='AWAITING_IMAGE_REVIEW', check_id='new-photo')
        self.assertIsNone(manager.snapshot()['image_validation'])


if __name__ == '__main__':
    unittest.main()
