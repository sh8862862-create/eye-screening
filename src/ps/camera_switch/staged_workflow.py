"""Gated eye capture -> saved-photo analysis -> log-only gesture review.

This new orchestration entry uses the existing CPU research/report adapter only
when explicitly requested. Existing FPGA paths and application defaults stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from sequential_camera import (positive_seconds, run_stage, save_json,
                               stop_worker, workflow_lock)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_roles(config):
    if config.get('roles_confirmed') is not True:
        raise ValueError('Camera roles require explicit user confirmation')
    devices = [str(Path(config[k]).resolve(strict=True)) for k in ('eye', 'gesture')]
    if devices[0] == devices[1]:
        raise ValueError('Eye and gesture roles must use different devices')
    return devices


def persist(session, record, state):
    record['state'] = state
    record.setdefault('events', []).append(dict(state=state, monotonic=time.monotonic()))
    save_json(session/'session.json', record)


def run_command(command, log_path, timeout):
    with log_path.open('x', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            stop_worker(process)
            raise
    if code != 0:
        raise RuntimeError('Stage failed with exit code %d; see %s' % (code, log_path))


def capture_eye(session, config, capture=run_stage):
    validate_roles(config)
    session.mkdir(parents=True, exist_ok=False)
    record = dict(schema_version=1, check_id=str(uuid.uuid4()), config=config,
                  control_outputs=False, clinical_validation=False)
    persist(session, record, 'EYE_CAPTURE')
    try:
        result = capture(config['eye'], session/'eye_capture', 30, 10, 640, 480, 45)
        image = session/'eye_capture'/'frame.png'
        record.update(capture=result, image=str(image.resolve()), image_sha256=digest(image))
        persist(session, record, 'AWAITING_IMAGE_REVIEW')
    except BaseException as exc:
        record['error'] = str(exc) or type(exc).__name__
        persist(session, record, 'ERROR')
        raise
    return record


def verify_image(record):
    image = Path(record['image'])
    if digest(image) != record['image_sha256']:
        raise ValueError('Captured image changed; create a new session')
    return image


def analyze_eye(session, confirmed_eye_image, backend, invoke=run_command):
    record = load_json(session/'session.json')
    if record['state'] != 'AWAITING_IMAGE_REVIEW':
        raise ValueError('Photo analysis requires a saved photo awaiting review')
    if not confirmed_eye_image:
        raise ValueError('Inspect the photo first; --confirm-eye-image is required')
    if backend != 'cpu':
        raise ValueError('This adapter supports only explicit CPU research analysis')
    image = verify_image(record)
    config = record['config']
    for key in ('eye_processor', 'eye_classifier', 'eye_localizer'):
        if not Path(config[key]).is_file():
            raise ValueError('Missing configured file: ' + config[key])
    output = session/'analysis'
    output.mkdir(exist_ok=False)
    record['image_review'] = dict(operator_confirmed_eye_image=True, automatic_quality_gate=False)
    persist(session, record, 'EYE_PROCESSING_CAMERAS_CLOSED')
    try:
        result_path, overlay, html = (output/'result.json', output/'result.png', output/'result.html')
        command = [sys.executable, config['eye_processor'], '--image', str(image),
                   '--classifier', config['eye_classifier'], '--localizer', config['eye_localizer'],
                   '--json', str(result_path), '--overlay', str(overlay), '--report', str(html),
                   '--threads', '2']
        invoke(command, output/'processor.log', 120)
        result = load_json(result_path)
        if Path(result.get('image', '')).resolve() != image.resolve():
            raise ValueError('Result refers to a different image')
        if not isinstance(result.get('classification'), dict):
            raise ValueError('Missing classification research result')
        for path in (result_path, overlay, html):
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Missing analysis artifact: ' + str(path))
        verify_image(record)
        record['analysis'] = dict(backend='cpu_research', result=str(result_path.resolve()),
                                  result_sha256=digest(result_path), report=str(html.resolve()),
                                  overlay=str(overlay.resolve()),
                                  report_sha256=digest(html), overlay_sha256=digest(overlay))
        persist(session, record, 'RESULT_READY')
    except BaseException as exc:
        record['error'] = str(exc) or type(exc).__name__
        persist(session, record, 'ERROR')
        raise
    return record


def gesture_test(session, seconds, invoke=run_command):
    record = load_json(session/'session.json')
    if record['state'] not in ('RESULT_READY', 'GESTURE_TEST_FINISHED'):
        raise ValueError('Gesture stage is blocked until this photo analysis succeeds')
    verify_image(record)
    config = record['config']
    validate_roles(config)
    analysis = record['analysis']
    for key in ('result', 'report', 'overlay'):
        if digest(analysis[key]) != analysis[key + '_sha256']:
            raise ValueError('Analysis artifact changed: ' + key)
    if not Path(config['gesture_script']).is_file():
        raise ValueError('Missing gesture script')
    node = Path(config['gesture']).resolve(strict=True).name
    if not re.fullmatch(r'video[0-9]+', node):
        raise ValueError('Gesture path did not resolve to a video device')
    attempt = len(record.get('gesture_tests', [])) + 1
    log = session/('gesture_%02d.log' % attempt)
    persist(session, record, 'GESTURE_TEST_LOG_ONLY')
    try:
        command = [sys.executable, config['gesture_script'], '--seconds', str(seconds),
                   '--camera', node[5:], '--rotate', str(config.get('gesture_rotation', 180)),
                   '--capture-mode', 'read', '--hand-backend', 'cpu']
        # No --send-to, and no capture subprocess children in read mode.
        invoke(command, log, seconds + 45)
        text = log.read_text(encoding='utf-8', errors='replace')
        if 'LOG ONLY:' not in text or 'Stopped; state cleared.' not in text:
            raise ValueError('Gesture program did not confirm log-only operation and shutdown')
        record.setdefault('gesture_tests', []).append(dict(log=str(log.resolve()),
                                                           seconds=seconds, control_outputs=False))
        persist(session, record, 'GESTURE_TEST_FINISHED')
    except BaseException as exc:
        record['error'] = str(exc) or type(exc).__name__
        persist(session, record, 'ERROR')
        raise
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('capture-eye', 'analyze-eye', 'gesture-test'):
        child = commands.add_parser(name)
        child.add_argument('--session', type=Path, required=True)
        if name == 'capture-eye':
            child.add_argument('--config', type=Path,
                               default=Path(__file__).with_name('camera_roles.json'))
        elif name == 'analyze-eye':
            child.add_argument('--confirm-eye-image', action='store_true')
            child.add_argument('--backend', choices=('cpu',), required=True,
                               help='Explicit research baseline; existing FPGA code is unchanged')
        else:
            child.add_argument('--seconds', type=positive_seconds, default=20)
    args = parser.parse_args()
    try:
        with workflow_lock(Path(__file__).with_name('sequential_camera.lock')):
            if args.command == 'capture-eye':
                record = capture_eye(args.session.resolve(), load_json(args.config))
            elif args.command == 'analyze-eye':
                record = analyze_eye(args.session.resolve(), args.confirm_eye_image, args.backend)
            else:
                record = gesture_test(args.session.resolve(), args.seconds)
        print(json.dumps(dict(check_id=record['check_id'], state=record['state'],
                              image=record['image'], control_outputs=False)), flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('STOPPED: ' + (str(exc) or type(exc).__name__), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
