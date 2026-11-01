"""Serial USB camera validation; no models, motors, illumination or control events.

Each stage owns one capture worker. A subsequent stage is allowed only after
successful capture, explicit release, and worker exit. Use stable by-path names.
"""
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def workflow_lock(path):
    """Cooperative Linux lock, held for the entire test including idle stages."""
    import fcntl
    with Path(path).open('a+') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another sequential camera workflow is already running')
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def capture_worker(args):
    import cv2 as cv
    cv.setNumThreads(1)
    cv.ocl.setUseOpenCL(False)
    report_path = args.output / 'capture.json'
    report = dict(device=args.device, resolved_device=str(Path(args.device).resolve()),
                  frames=0, warmup_frames=0, released=False, status='opening',
                  started_monotonic=time.monotonic(), control_outputs=False)
    save_json(report_path, report)
    cap = None
    try:
        cap = cv.VideoCapture(args.device, cv.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError('Camera could not be opened')
        cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*'YUYV'))
        cap.set(cv.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, args.height)
        report.update(status='capturing', width=cap.get(cv.CAP_PROP_FRAME_WIDTH),
                      height=cap.get(cv.CAP_PROP_FRAME_HEIGHT),
                      fourcc=int(cap.get(cv.CAP_PROP_FOURCC)),
                      reported_fps=cap.get(cv.CAP_PROP_FPS))
        save_json(report_path, report)
        for index in range(args.warmup + args.frames):
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                raise RuntimeError('Camera returned no valid frame')
            if index < args.warmup:
                report['warmup_frames'] += 1
            else:
                report['frames'] += 1
                report.setdefault('first_frame_monotonic', time.monotonic())
                report['last_frame_monotonic'] = time.monotonic()
            if (index + 1) % 10 == 0:
                save_json(report_path, report)
        image = args.output / 'frame.png'
        if not cv.imwrite(str(image), frame):
            raise RuntimeError('Unable to save image')
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        report.update(image=str(image.resolve()), gray_mean=float(gray.mean()),
                      gray_std=float(gray.std()), status='releasing')
    except Exception as exc:
        report.update(status='error', error=str(exc))
    finally:
        save_json(report_path, report)
        # If release blocks, the supervisor times out and refuses the next stage.
        try:
            if cap is not None:
                cap.release()
                if cap.isOpened():
                    raise RuntimeError('Camera is still open after release')
            report['released'] = True
            report['released_monotonic'] = time.monotonic()
        except Exception as exc:
            report.update(status='error', error='Release failed: ' + str(exc))
        if report['status'] == 'releasing' and report['released']:
            report['status'] = 'completed'
        save_json(report_path, report)
    return 0 if report['status'] == 'completed' else 2


def stop_worker(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run_stage(device, output, frames, warmup, width, height, timeout):
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, '-u', str(Path(__file__).resolve()), '_capture',
               '--device', device, '--output', str(output), '--frames', str(frames),
               '--warmup', str(warmup), '--width', str(width), '--height', str(height)]
    with (output / 'worker.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            exitcode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_worker(process)
            raise RuntimeError('Capture/release timed out; next camera will NOT be opened')
        except BaseException:
            stop_worker(process)
            raise
    path = output / 'capture.json'
    report = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    image = output / 'frame.png'
    if (exitcode != 0 or report.get('status') != 'completed'
            or report.get('released') is not True or report.get('frames') != frames
            or not image.is_file() or image.stat().st_size == 0):
        raise RuntimeError('Incomplete capture/release: ' + str(output))
    report['worker_exitcode'] = exitcode
    report['worker_exited_monotonic'] = time.monotonic()
    return report


def switch_test(args, stage_runner=run_stage):
    resolved = [str(Path(device).resolve(strict=True)) for device in args.devices]
    if len(set(resolved)) != 2:
        raise ValueError('A and B must be two different devices')
    args.output.mkdir(parents=True, exist_ok=False)
    summary = dict(status='running', control_outputs=False, roles_assigned=False,
                   devices=args.devices, resolved_devices=resolved, cycles=args.cycles,
                   stages=[], note='Capture switching test only; no eye model or gesture inference')
    summary_path = args.output / 'summary.json'
    save_json(summary_path, summary)
    try:
        for cycle in range(1, args.cycles + 1):
            for label, device, expected in zip(('A', 'B'), args.devices, resolved):
                if str(Path(device).resolve(strict=True)) != expected:
                    raise RuntimeError('Device mapping changed; reconnect and confirm roles')
                stage = '%02d_%s' % (cycle, label)
                print('START ' + stage + ' ' + device, flush=True)
                report = stage_runner(device, args.output / stage, args.frames, args.warmup,
                                      args.width, args.height, args.timeout)
                report.update(stage=stage, cycle=cycle, label=label)
                summary['stages'].append(report)
                save_json(summary_path, summary)
                print('CLOSED ' + stage + ' frames=' + str(report['frames']), flush=True)
        summary['status'] = 'completed'
    except BaseException as exc:
        summary.update(status='stopped', error=str(exc) or type(exc).__name__)
        save_json(summary_path, summary)
        raise
    save_json(summary_path, summary)
    return summary


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return result


def positive_seconds(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('switch-test', '_capture'):
        child = commands.add_parser(name)
        child.add_argument('--output', type=Path, required=True)
        child.add_argument('--frames', type=positive_int, default=30)
        child.add_argument('--warmup', type=positive_int, default=10)
        child.add_argument('--width', type=positive_int, default=640)
        child.add_argument('--height', type=positive_int, default=480)
        if name == '_capture':
            child.add_argument('--device', required=True)
        else:
            child.add_argument('--devices', nargs=2, required=True)
            child.add_argument('--cycles', type=positive_int, default=3)
            child.add_argument('--timeout', type=positive_seconds, default=45)
    args = parser.parse_args()
    if args.command == '_capture':
        return capture_worker(args)
    if os.name != 'posix':
        parser.error('Camera switch tests must run on the Linux PYNQ board')
    try:
        with workflow_lock(Path(__file__).resolve().with_suffix('.lock')):
            summary = switch_test(args)
        print(json.dumps(dict(status=summary['status'], stages=len(summary['stages']))), flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('STOPPED: ' + (str(exc) or type(exc).__name__), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
