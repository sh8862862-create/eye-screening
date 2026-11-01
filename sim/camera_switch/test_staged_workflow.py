import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import staged_workflow as subject


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = self.root/'session'
        self.config = dict(roles_confirmed=True, gesture_rotation=180)
        for key, name in [('eye', 'video2'), ('gesture', 'video0'),
                          ('eye_processor', 'infer.py'), ('eye_classifier', 'c.onnx'),
                          ('eye_localizer', 'l.onnx'), ('gesture_script', 'live.py')]:
            path = self.root/name
            path.touch()
            self.config[key] = str(path)

    def capture(self):
        def fake(device, output, *args):
            self.assertEqual(device, self.config['eye'])
            output.mkdir()
            (output/'frame.png').write_bytes(b'unit-test-image-not-real-eye')
            return dict(released=True, status='completed')
        return subject.capture_eye(self.session, self.config, fake)

    def processor(self, command, log, timeout):
        def arg(name):
            return Path(command[command.index(name)+1])
        log.write_text('test processor')
        arg('--json').write_text(json.dumps(dict(image=str(arg('--image')), classification={})))
        arg('--overlay').write_bytes(b'test-overlay')
        arg('--report').write_text('test-report')

    def test_capture_then_human_gate(self):
        record = self.capture()
        self.assertEqual(record['state'], 'AWAITING_IMAGE_REVIEW')
        invoke = Mock()
        with self.assertRaisesRegex(ValueError, 'Inspect the photo'):
            subject.analyze_eye(self.session, False, 'cpu', invoke)
        invoke.assert_not_called()

    def test_no_gesture_before_analysis(self):
        self.capture()
        invoke = Mock()
        with self.assertRaisesRegex(ValueError, 'blocked'):
            subject.gesture_test(self.session, 20, invoke)
        invoke.assert_not_called()

    def test_full_order_with_mock_processor(self):
        self.capture()
        result = subject.analyze_eye(self.session, True, 'cpu', self.processor)
        self.assertEqual(result['state'], 'RESULT_READY')
        def gesture(command, log, timeout):
            self.assertEqual(command[command.index('--camera')+1], '0')
            self.assertNotIn('--send-to', command)
            log.write_text('LOG ONLY:\nStopped; state cleared.')
        result = subject.gesture_test(self.session, 20, gesture)
        self.assertEqual(result['state'], 'GESTURE_TEST_FINISHED')
        self.assertFalse(result['control_outputs'])

    def test_processing_error_stops_workflow(self):
        self.capture()
        with self.assertRaises(RuntimeError):
            subject.analyze_eye(self.session, True, 'cpu', Mock(side_effect=RuntimeError('test failure')))
        self.assertEqual(subject.load_json(self.session/'session.json')['state'], 'ERROR')
        with self.assertRaises(ValueError):
            subject.gesture_test(self.session, 20, Mock())

    def test_changed_image_rejected(self):
        record = self.capture()
        Path(record['image']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            subject.analyze_eye(self.session, True, 'cpu', Mock())

    def test_changed_report_rejected(self):
        self.capture()
        result = subject.analyze_eye(self.session, True, 'cpu', self.processor)
        Path(result['analysis']['report']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            subject.gesture_test(self.session, 20, Mock())

    def test_unconfirmed_roles_rejected(self):
        self.config['roles_confirmed'] = False
        with self.assertRaises(ValueError):
            subject.capture_eye(self.session, self.config, Mock())

    def test_wrong_image_result_rejected(self):
        self.capture()
        def wrong_processor(command, log, timeout):
            self.processor(command, log, timeout)
            path = Path(command[command.index('--json')+1])
            path.write_text(json.dumps(dict(image='other.png', classification={})))
        with self.assertRaisesRegex(ValueError, 'different image'):
            subject.analyze_eye(self.session, True, 'cpu', wrong_processor)


if __name__ == '__main__':
    unittest.main()
