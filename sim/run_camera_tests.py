"""Camera workflow tests with simulated capture; Linux-only process tests self-skip."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
           PYTHONPATH=str(root/'src/ps/camera_switch'))
raise SystemExit(subprocess.call([sys.executable, '-B', '-m', 'unittest', 'discover',
                                 '-s', str(root/'sim/camera_switch'), '-v'], env=env, cwd=root))
