"""Run isolated controller/tracker regression tests without hardware or HID."""
import os
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env['PYTHONDONTWRITEBYTECODE'] = '1'
env['PYTHONPATH'] = os.pathsep.join([str(root / 'src/ps/gesture'), str(root / 'sim/gesture')])
raise SystemExit(subprocess.call(
    [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(root / 'sim/gesture'), '-p', 'test_*.py', '-v'],
    env=env, cwd=root,
))
