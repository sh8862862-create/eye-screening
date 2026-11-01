"""Supervisor tests without hardware or OpenCV."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import sequential_camera as subject


class SequentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.devices = [self.root / 'a', self.root / 'b']
        for device in self.devices:
            device.touch()
        self.args = argparse.Namespace(devices=list(map(str, self.devices)), output=self.root/'run',
                                       cycles=3, frames=30, warmup=10, width=640, height=480, timeout=1)

    def test_order_and_complete(self):
        calls = []
        def runner(device, *unused):
            calls.append(device)
            return {'frames': 30, 'released': True}
        result = subject.switch_test(self.args, runner)
        self.assertEqual(calls, self.args.devices * 3)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['stages']), 6)

    def test_failure_prevents_next_camera(self):
        runner = Mock(side_effect=RuntimeError('close failed'))
        with self.assertRaisesRegex(RuntimeError, 'close failed'):
            subject.switch_test(self.args, runner)
        self.assertEqual(runner.call_count, 1)
        result = json.loads((self.args.output/'summary.json').read_text())
        self.assertEqual(result['status'], 'stopped')

    def test_same_device_rejected(self):
        self.args.devices[1] = self.args.devices[0]
        runner = Mock()
        with self.assertRaises(ValueError):
            subject.switch_test(self.args, runner)
        runner.assert_not_called()

    def test_existing_output_not_overwritten(self):
        self.args.output.mkdir()
        with self.assertRaises(FileExistsError):
            subject.switch_test(self.args, Mock())

    def test_report_failure_even_with_zero_exit(self):
        process = Mock()
        process.wait.return_value = 0
        with patch.object(subject.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                subject.run_stage(str(self.devices[0]), self.root/'stage', 30, 10, 640, 480, 1)

    def test_success_requires_release_and_saved_image(self):
        output = self.root/'stage'
        def completed_worker(*unused, **kwargs):
            subject.save_json(output/'capture.json',
                              {'status': 'completed', 'released': True, 'frames': 30})
            (output/'frame.png').write_bytes(b'test-image')
            return 0
        process = Mock()
        process.wait.side_effect = completed_worker
        with patch.object(subject.subprocess, 'Popen', return_value=process):
            result = subject.run_stage(str(self.devices[0]), output, 30, 10, 640, 480, 1)
        self.assertEqual(result['worker_exitcode'], 0)
        self.assertIn('worker_exited_monotonic', result)

    def test_incomplete_release_rejected(self):
        output = self.root/'stage'
        def incomplete_worker(*unused, **kwargs):
            subject.save_json(output/'capture.json',
                              {'status': 'completed', 'released': False, 'frames': 30})
            (output/'frame.png').write_bytes(b'test-image')
            return 0
        process = Mock()
        process.wait.side_effect = incomplete_worker
        with patch.object(subject.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                subject.run_stage(str(self.devices[0]), output, 30, 10, 640, 480, 1)

    def test_timeout_stops_worker(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 1), 0]
        with patch.object(subject.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'next camera will NOT'):
                subject.run_stage(str(self.devices[0]), self.root/'stage', 30, 10, 640, 480, 1)
        process.terminate.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_timeout_validation(self):
        for value in ('0', '-1', 'nan', 'inf'):
            with self.assertRaises(argparse.ArgumentTypeError):
                subject.positive_seconds(value)


if __name__ == '__main__':
    unittest.main()
