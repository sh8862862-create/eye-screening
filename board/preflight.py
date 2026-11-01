"""Read-only installed-system checks. No FPGA load, camera open, or HID output."""
import argparse
import ctypes
import importlib
import json
from pathlib import Path
import platform
import subprocess
import sys

BASE = Path('/home/xilinx/gesture_demo')
checks = []


def check(name, operation):
    try:
        detail = operation()
        checks.append(dict(name=name, ok=True, detail=detail))
    except Exception as exc:
        checks.append(dict(name=name, ok=False, error=str(exc)))


def require(condition, message):
    if not condition:
        raise ValueError(message)
    return message


def camera_roles():
    d = json.loads((BASE/'camera_switch/camera_roles.json').read_text())
    require(d.get('roles_confirmed') is True, 'Physical roles must be confirmed')
    a, b = [Path(d[k]).resolve(strict=True) for k in ('eye', 'gesture')]
    require(a != b and a.is_char_device() and b.is_char_device(), 'Distinct attached camera devices required')
    return {k:d[k] for k in ('eye', 'gesture')}


def workflow_imports():
    sys.path.insert(0, str(BASE/'camera_switch'))
    sys.path.insert(0, str(BASE/'camera_web'))
    sys.path.insert(0, str(BASE/'camera_web/vendor'))
    for name in ['staged_workflow', 'sequential_camera', 'workflow_process', 'service', 'eye_presence']:
        importlib.import_module(name)
    return 'Imported workflow and web without starting them'


def cv_runtime():
    import cv2
    require(hasattr(cv2, 'CascadeClassifier') and hasattr(cv2, 'dnn'), 'Full OpenCV requires objdetect and DNN')
    return dict(version=cv2.__version__, file=cv2.__file__)


def palm_runtime():
    python = '/usr/local/share/pynq-venv/bin/python3'
    import os
    env = dict(os.environ, PYTHONPATH='/opt/opencv-neon/lib/python3.10/site-packages',
               LD_LIBRARY_PATH='/opt/opencv-neon/lib')
    code = "import cv2,ctypes; ctypes.CDLL('/opt/pynq-palm/libpalm_layer.so'); print(cv2.__version__,cv2.__file__)"
    p = subprocess.run([python, '-c', code], env=env, capture_output=True, text=True, timeout=30)
    require(p.returncode == 0, p.stderr)
    return p.stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    check('platform', lambda: require(sys.platform.startswith('linux') and platform.machine() in ('armv7l','armv8l'), 'ARMv7 Linux required'))
    check('camera_roles', camera_roles)
    check('workflow_imports', workflow_imports)
    for module in ['pynq', 'numpy', 'PIL', 'waitress']:
        check('import_'+module, lambda m=module: str(importlib.import_module(m).__file__))
    check('full_opencv', cv_runtime)
    check('palm_abi', palm_runtime)
    check('ncnn_abi', lambda: str(ctypes.CDLL('/opt/pynq-gesture/libgesture_ncnn.so')._name))
    check('chinese_font', lambda: require(any(Path(p).is_file() for p in [
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
        '/opt/pynq-shared/fonts/droidsansfallbackfull.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']), 'A supported Chinese font is required'))
    units = ['pynq-camera-web','pynq-fpga','pynq-palm','pynq-buttons','ch9329-hid','eye-oled']
    for u in units:
        check('unit_'+u, lambda unit=u: require(Path('/etc/systemd/system/'+unit+'.service').is_file(), unit))
    result = dict(ok=all(c['ok'] for c in checks), read_only=True, checks=checks)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if a.output:
        with a.output.open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
