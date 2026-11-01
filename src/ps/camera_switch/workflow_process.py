"""Linux stage supervision: reap the command AND its owned camera descendants.

No camera/model imports. Each command gets its own process group. A forced
shutdown is always a failed stage, even when the process handles SIGINT as a
normal exit. Processes outside that group are never signalled.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def live_group_members(pgid, proc_root=Path('/proc')):
    """Find live group members; zombies have exited and hold no camera handles.

    Linux procfs is required. Unreadable process state is an error rather than
    evidence of successful cleanup. A disappearing PID is harmless.
    """
    if pgid <= 1 or pgid == os.getpgrp():
        raise RuntimeError('Refusing to inspect/signal the supervisor process group')
    members = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # comm may contain spaces and parentheses; fields after it start at state.
            stat = (entry / 'stat').read_text(errors='replace')
        except (FileNotFoundError, ProcessLookupError):
            continue
        fields = stat[stat.rfind(')') + 2:].split()
        if len(fields) < 3:
            raise RuntimeError('Cannot verify process state: ' + str(entry))
        if int(fields[2]) == pgid and fields[0] not in ('Z', 'X'):
            members.append(int(entry.name))
    return members


def signal_group(pgid, sig):
    if pgid <= 1 or pgid == os.getpgrp():
        raise RuntimeError('Refusing to signal the supervisor process group')
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def wait_empty(process, seconds):
    deadline = time.monotonic() + seconds
    while True:
        process.poll()  # Reap the direct child before checking procfs.
        if not live_group_members(process.pid):
            process.wait(timeout=1)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.05)


def try_wait_empty(process, seconds):
    try:
        return wait_empty(process, seconds)
    except (OSError, RuntimeError, ValueError):
        # Verification failure is not success, but must not prevent escalation.
        return False


def stop_owned_group(process, grace=12., term_grace=3., kill_grace=3.):
    # A second Ctrl+C must not interrupt bounded group cleanup.
    in_main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN) if in_main_thread else None
    try:
        _stop_owned_group(process, grace, term_grace, kill_grace)
    finally:
        if in_main_thread:
            signal.signal(signal.SIGINT, previous)


def _stop_owned_group(process, grace, term_grace, kill_grace):
    # The camera worker ignores SIGINT. Signal only its Python parent first so
    # live_gesture_log's finally block has time to close/reap that child itself.
    if process.poll() is None:
        try:
            process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
        if try_wait_empty(process, grace):
            return
    elif try_wait_empty(process, 0.):
        return
    for sig, delay in ((signal.SIGTERM, term_grace), (signal.SIGKILL, kill_grace)):
        signal_group(process.pid, sig)
        if try_wait_empty(process, delay):
            return
    raise RuntimeError('Owned process group did not stop; do not open either camera')


def run_owned_command(command, log_path, timeout):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Hardware stages require Linux process-group supervision')
    with Path(log_path).open('x', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
            # multiprocessing's resource_tracker may finish just after its parent.
            if not wait_empty(process, 2.):
                raise RuntimeError('Stage exited with live descendants; refusing success')
        except BaseException:
            stop_owned_group(process)
            raise
    if code != 0:
        raise RuntimeError('Stage failed with exit code %d; see %s' % (code, log_path))
