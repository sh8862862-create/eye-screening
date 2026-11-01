"""Restore the captured OpenCV runtime to a fresh rootfs, after hash verification.

This optional runtime is ARMv7/Python 3.10 specific. It does not replace the OS,
PYNQ base image, Python, glibc, XRT, or the full OpenCV used by the eye gate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def restore(rootfs, archive, expected, apply=False):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('Runtime archive hash mismatch')
    destination = rootfs/'opt/opencv-neon'
    if destination.exists() or destination.is_symlink():
        raise ValueError('Existing runtime; preserve it and use a fresh rootfs')
    for p in destination.parents:
        if p == rootfs:
            break
        if p.is_symlink():
            raise ValueError('Symlink in destination')
    with tarfile.open(archive, 'r:gz') as tf:
        members = tf.getmembers()
        names = set()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or p.parts[:2] != ('opt', 'opencv-neon'):
                raise ValueError('Unexpected runtime path: '+m.name)
            if m.name in names:
                raise ValueError('Duplicate archive member')
            names.add(m.name)
            if not (m.isfile() or m.isdir() or m.issym()):
                raise ValueError('Unsupported archive member')
            if m.issym():
                link = PurePosixPath(m.linkname)
                # Captured library symlinks point to a basename in the same directory.
                if link.is_absolute() or len(link.parts) != 1 or m.linkname in ('.','..'):
                    raise ValueError('Unexpected library symlink: '+m.linkname)
        links = {m.name for m in members if m.issym()}
        for m in members:
            if any(str(p) in links for p in PurePosixPath(m.name).parents):
                raise ValueError('Archive traverses symlink')
        if apply:
            if rootfs.parent == rootfs and (not sys.platform.startswith('linux') or os.geteuid() != 0 or platform.machine() not in ('armv7l','armv8l')):
                raise ValueError('Live restore requires ARMv7 root')
            try:
                for m in members:
                    p = rootfs/m.name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if m.isdir():
                        p.mkdir(exist_ok=True)
                    elif m.isfile():
                        with tf.extractfile(m) as source, p.open('xb') as out:
                            shutil.copyfileobj(source, out)
                        p.chmod(m.mode & 0o777)
                for m in members:
                    if m.issym():
                        (rootfs/m.name).symlink_to(m.linkname)
            except BaseException:
                # Remove only the verified newly created runtime subtree.
                if destination.resolve().is_relative_to(rootfs.resolve()) and destination.is_dir():
                    shutil.rmtree(destination)
                raise
    return dict(files=len(members), applied=apply, target=str(destination))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rootfs', type=Path, default=Path('/'))
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    info = json.loads((ROOT/'data/runtime/manifest.json').read_text())
    print(json.dumps(restore(a.rootfs.resolve(), ROOT/info['path'], info['sha256'], a.apply), indent=2))
