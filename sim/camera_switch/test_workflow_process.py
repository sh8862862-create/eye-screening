"""No-camera tests; real process-tree cases run only on the Linux board."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import workflow_process as subject


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_proc_parser_handles_spaces_parentheses_and_zombies(self):
        for pid, comm, state, group in [(100, 'a tricky ) name', 'S', 9876),
                                        (101, 'dead', 'Z', 9876), (102, 'other', 'R', 4567)]:
            directory = self.root / str(pid)
            directory.mkdir()
            (directory / 'stat').write_text(f'{pid} ({comm}) {state} 1 {group} 0 0')
        with patch.object(subject.os, 'getpgrp', return_value=99, create=True):
            self.assertEqual(subject.live_group_members(9876, self.root), [100])

    def test_never_signal_own_group(self):
        with patch.object(subject.os, 'getpgrp', return_value=99, create=True), \
                patch.object(subject.os, 'killpg', create=True) as kill:
            for pgid in (0, 1, 99):
                with self.assertRaises(RuntimeError):
                    subject.signal_group(pgid, signal.SIGTERM)
            kill.assert_not_called()

    def test_unreadable_process_state_is_not_success(self):
        directory = self.root / '100'
        directory.mkdir()
        (directory / 'stat').write_text('malformed')
        with patch.object(subject.os, 'getpgrp', return_value=99, create=True):
            with self.assertRaises(RuntimeError):
                subject.live_group_members(9876, self.root)

    def test_timeout_cleans_whole_owned_group(self):
        process = Mock(pid=9876)
        process.wait.side_effect = subprocess.TimeoutExpired('fixture', .01)
        with patch.object(subject.sys, 'platform', 'linux'), \
                patch.object(subject.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(subject, 'stop_owned_group') as stop:
            with self.assertRaises(subprocess.TimeoutExpired):
                subject.run_owned_command(['fixture'], self.root/'log', .01)
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            stop.assert_called_once_with(process)

    def test_parent_success_with_live_child_is_rejected(self):
        process = Mock(pid=9876)
        process.wait.return_value = 0
        with patch.object(subject.sys, 'platform', 'linux'), \
                patch.object(subject.subprocess, 'Popen', return_value=process), \
                patch.object(subject, 'wait_empty', return_value=False), \
                patch.object(subject, 'stop_owned_group') as stop:
            with self.assertRaisesRegex(RuntimeError, 'live descendants'):
                subject.run_owned_command(['fixture'], self.root/'log', 1)
            stop.assert_called_once_with(process)

    def test_normal_exit_allows_bounded_descendant_drain(self):
        process = Mock(pid=9876)
        process.wait.return_value = 0
        with patch.object(subject.sys, 'platform', 'linux'), \
                patch.object(subject.subprocess, 'Popen', return_value=process), \
                patch.object(subject, 'wait_empty', return_value=True) as drain:
            subject.run_owned_command(['fixture'], self.root/'log', 1)
            drain.assert_called_once_with(process, 2.)

    def test_cleanup_defers_interrupt_and_restores_handler(self):
        process = Mock(pid=9876)
        with patch.object(subject.signal, 'signal', return_value='previous') as set_signal, \
                patch.object(subject, '_stop_owned_group', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                subject.stop_owned_group(process)
            self.assertEqual(set_signal.call_args_list[0].args, (signal.SIGINT, signal.SIG_IGN))
            self.assertEqual(set_signal.call_args_list[1].args, (signal.SIGINT, 'previous'))

    def test_proc_verification_failure_still_signals_owned_group(self):
        process = Mock(pid=9876)
        process.poll.return_value = 0
        with patch.object(subject, 'wait_empty', side_effect=PermissionError('fixture')), \
                patch.object(subject.signal, 'SIGKILL', 9, create=True), \
                patch.object(subject, 'signal_group') as kill:
            with self.assertRaisesRegex(RuntimeError, 'did not stop'):
                subject._stop_owned_group(process, .1, .1, .1)
            self.assertEqual(kill.call_count, 2)

    def test_cleanup_failure_is_not_hidden(self):
        process = Mock(pid=9876)
        process.wait.side_effect = subprocess.TimeoutExpired('fixture', .01)
        with patch.object(subject.sys, 'platform', 'linux'), \
                patch.object(subject.subprocess, 'Popen', return_value=process), \
                patch.object(subject, 'stop_owned_group', side_effect=RuntimeError('not stopped')):
            with self.assertRaisesRegex(RuntimeError, 'not stopped'):
                subject.run_owned_command(['fixture'], self.root/'log', .01)

    def test_nonzero_stage_exit_fails(self):
        process = Mock(pid=9876)
        process.wait.return_value = 3
        with patch.object(subject.sys, 'platform', 'linux'), \
                patch.object(subject.subprocess, 'Popen', return_value=process), \
                patch.object(subject, 'live_group_members', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'exit code 3'):
                subject.run_owned_command(['fixture'], self.root/'log', 1)


@unittest.skipUnless(sys.platform.startswith('linux'), 'Requires Linux process groups')
class LinuxProcessTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.processes = []
        self.addCleanup(self.clean_processes)

    def clean_processes(self):
        for process in self.processes:
            subject.stop_owned_group(process, grace=.2, term_grace=.5, kill_grace=1.)

    def spawn_tree(self, parent_exits=False):
        ready = self.root / 'ready'
        child_pid_path = self.root / 'child_pid'
        child_code = ('import signal,time; '
                      'signal.signal(signal.SIGINT,signal.SIG_IGN); '
                      'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                      f'open({str(ready)!r},"w").write("ready"); '
                      'time.sleep(60)')
        parent_code = ('import subprocess,sys,signal,time,pathlib; '
                       'signal.signal(signal.SIGINT,signal.SIG_IGN); '
                       'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                       f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
                       f'pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid)); '
                       + ('sys.exit(0)' if parent_exits else 'time.sleep(60)'))
        process = subprocess.Popen([sys.executable, '-c', parent_code], start_new_session=True)
        self.processes.append(process)
        import time
        deadline = time.monotonic() + 10
        while (not ready.exists() or not child_pid_path.exists()) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(ready.exists(), 'fixture child did not start')
        self.assertTrue(child_pid_path.exists(), 'fixture parent did not publish child PID')
        return process, int(child_pid_path.read_text())

    def test_kills_blocked_parent_and_child_not_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                     start_new_session=True)
        self.processes.append(unrelated)
        process, child = self.spawn_tree()
        subject.stop_owned_group(process, grace=.1, term_grace=.1, kill_grace=2.)
        self.assertEqual(subject.live_group_members(process.pid), [])
        self.assertIsNone(unrelated.poll())

    def test_cleans_child_after_parent_already_exited(self):
        process, child = self.spawn_tree(parent_exits=True)
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIn(child, subject.live_group_members(process.pid))
        subject.stop_owned_group(process, grace=.1, term_grace=.1, kill_grace=2.)
        self.assertEqual(subject.live_group_members(process.pid), [])

    def test_real_success_reaps_child(self):
        code = ('import multiprocessing as mp,time; '
                'p=mp.get_context("spawn").Process(target=time.sleep,args=(.01,)); '
                'p.start(); p.join(); print("fixture OK")')
        subject.run_owned_command([sys.executable, '-c', code],
                                  self.root/'log', 10)
        self.assertIn('fixture OK', (self.root/'log').read_text())

    def test_real_timeout_keeps_failure(self):
        with patch.object(subject, 'stop_owned_group',
                          wraps=lambda p: ORIGINAL_STOP(p, grace=.1, term_grace=.2, kill_grace=2.)):
            with self.assertRaises(subprocess.TimeoutExpired):
                subject.run_owned_command([sys.executable, '-c', 'import time; time.sleep(60)'],
                                          self.root/'log', .1)


ORIGINAL_STOP = subject.stop_owned_group

if __name__ == '__main__':
    unittest.main()
