"""Bounded eye-presence check before the saved-photo classification workflow.

Haar eye models screen out unsupported scenes; this is not a clinical quality
assessment, liveness test, or a guarantee that every eye will be detected.
"""
import time
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


NO_EYE_MESSAGE = '未识别到眼睛，请调整摄像头后重新拍摄。'
MODEL_NAMES = ('haarcascade_eye.xml', 'haarcascade_eye_tree_eyeglasses.xml')
MODEL_DIR = Path(__file__).resolve().parent / 'eye_presence_models'


def isolated_environment():
    """Remove only the gesture-only OpenCV override in a new child process."""
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'LD_LIBRARY_PATH'):
        paths = [p for p in env.get(key, '').split(os.pathsep)
                 if p and not p.rstrip('/').startswith('/opt/opencv-neon')]
        if paths:
            env[key] = os.pathsep.join(paths)
        else:
            env.pop(key, None)
    return env


def detect_eye(image_path, model_dir=MODEL_DIR, allow_isolated=True):
    start = time.monotonic()
    if not hasattr(cv2, 'CascadeClassifier'):
        if allow_isolated:
            # The web service imports a minimal NEON OpenCV for gestures.
            # Loading another cv2 into that process is unsafe; use the existing
            # system OpenCV in a bounded child instead, preserving the parent.
            try:
                child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                        '--isolated-detect', str(Path(image_path).resolve()),
                                        str(Path(model_dir).resolve())],
                                       env=isolated_environment(), capture_output=True,
                                       text=True, encoding='utf-8', timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError('眼部检测超时，请重新尝试。') from exc
            if child.returncode:
                raise RuntimeError('眼部检测组件执行失败：' + child.stderr.strip()[-500:])
            try:
                result = json.loads(child.stdout)
                if (not isinstance(result, dict) or type(result.get('detected')) is not bool
                        or result.get('status') != ('eye_detected' if result['detected'] else 'no_eye')):
                    raise ValueError('invalid detector result')
            except (ValueError, TypeError) as exc:
                raise RuntimeError('眼部检测返回无效结果，暂不能分析。') from exc
            result['isolated_process_ms'] = round((time.monotonic()-start)*1000, 1)
            return result
        raise RuntimeError('眼部检测组件不可用，暂不能分析，请检查 OpenCV objdetect。')
    data = np.fromfile(str(image_path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ValueError('照片无法解码，请重新拍摄。')
    models = []
    for name in MODEL_NAMES:
        path = Path(model_dir) / name
        if not path.is_file():
            raise RuntimeError('眼部检测模型缺失，暂不能分析：' + name)
        model = cv2.CascadeClassifier(str(path))
        if model.empty():
            raise RuntimeError('眼部检测模型无法加载，暂不能分析：' + name)
        models.append(model)
    cv2.setNumThreads(2)
    height, width = image.shape[:2]
    scale = 320.0 / max(height, width)
    gray = cv2.cvtColor(cv2.resize(image, (max(1, round(width * scale)),
                                         max(1, round(height * scale)))), cv2.COLOR_BGR2GRAY)
    # Replicated margins let large eye close-ups fit inside a cascade window.
    gray = cv2.equalizeHist(gray)
    result = dict(status='no_eye', detected=False, message=NO_EYE_MESSAGE,
                  method='opencv_haar_eye_v1', image_size=[width, height],
                  runtime=dict(opencv_version=cv2.__version__, opencv_file=cv2.__file__,
                               isolated=not allow_isolated))
    # Fixed four passes: camera orientation must not change the gate outcome.
    for angle in (0, 90, 180, 270):
        h, w = gray.shape
        pad = 80
        padded = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        for name, model in zip(MODEL_NAMES, models):
            boxes = model.detectMultiScale(padded, scaleFactor=1.1, minNeighbors=4,
                                           minSize=(32, 32))
            for x, y, bw, bh in boxes:
                # Ignore edge-padding detections; most of the window must lie
                # in the real image and its centre must be inside that image.
                overlap = max(0, min(x+bw, pad+w)-max(x, pad)) * max(0, min(y+bh, pad+h)-max(y, pad))
                if (pad <= x+bw/2 < pad+w and pad <= y+bh/2 < pad+h
                        and overlap >= .70 * bw * bh):
                    result.update(status='eye_detected', detected=True, message='',
                                  detector=name, rotation=angle,
                                  box_in_rotated_scaled_image=[int(x-pad), int(y-pad), int(bw), int(bh)])
                    result['elapsed_ms'] = round((time.monotonic()-start)*1000, 1)
                    return result
        gray = cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    result['elapsed_ms'] = round((time.monotonic()-start)*1000, 1)
    return result


def validate_saved_eye(session, detect=detect_eye):
    """Verify the capture hash, persist gate evidence, and allow safe retakes."""
    from staged_workflow import load_json, persist, verify_image
    session = Path(session)
    record = load_json(session / 'session.json')
    if record.get('state') != 'AWAITING_IMAGE_REVIEW':
        raise ValueError('当前没有等待确认的眼部照片。')
    image = verify_image(record)
    result = detect(image)
    # Do not accept a photo changed while the detector was reading it.
    verify_image(record)
    result['image_sha256'] = record['image_sha256']
    record['image_validation'] = result
    if not result['detected']:
        record.pop('analysis', None)
    persist(session, record, 'AWAITING_IMAGE_REVIEW')
    return record


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--isolated-detect':
        raise SystemExit('Usage: eye_presence.py --isolated-detect IMAGE MODEL_DIR')
    try:
        print(json.dumps(detect_eye(sys.argv[2], sys.argv[3], allow_isolated=False)))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
