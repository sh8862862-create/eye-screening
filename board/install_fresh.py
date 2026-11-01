"""Plan or install a verified payload into a fresh rootfs. Never start hardware.

Existing different files are rejected before mutation. This is not an upgrader.
Use --rootfs to stage and test without changing the running system.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skill/pynq-eye-system/scripts'))
import project


def destination(rootfs, target):
    if not target.startswith(('/home/xilinx/', '/opt/', '/etc/systemd/')):
        raise ValueError('Unexpected destination: ' + target)
    parts = target.lstrip('/').split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ValueError('Invalid destination: ' + target)
    p = rootfs.joinpath(*parts)
    # Reject all symlink components, including broken links, before installation.
    for parent in [p, *p.parents]:
        if parent == rootfs:
            break
        if parent.is_symlink():
            raise ValueError('Symlink destination: ' + str(parent))
    if not p.resolve().is_relative_to(rootfs.resolve()):
        raise ValueError('Destination escapes rootfs')
    return p


def plan(rootfs, camera_config):
    release = project.release_plan(ROOT)
    rows = []
    for row in release['files']:
        rows.append({**row, 'source_path': str(ROOT/row['source'])})
    config = json.loads(camera_config.read_text(encoding='utf-8'))
    if config.get('roles_confirmed') is not True or config.get('eye') == config.get('gesture'):
        raise ValueError('Explicit distinct camera roles required')
    if not all(config.get(k, '').startswith('/dev/v4l/by-path/') for k in ('eye', 'gesture')):
        raise ValueError('Camera roles must use stable by-path names')
    rows.append(dict(source_path=str(camera_config.resolve()),
                     target='/home/xilinx/gesture_demo/camera_switch/camera_roles.json',
                     mode='0644', sha256=project.sha(camera_config)))
    conflicts = []
    for row in rows:
        p = destination(rootfs, row['target'])
        row['destination'] = str(p)
        row['already_matches'] = p.is_file() and project.sha(p) == row['sha256']
        if p.exists() and not row['already_matches']:
            conflicts.append(str(p))
    if conflicts:
        raise ValueError('Existing files differ; use a fresh image/rootfs, not this installer: ' + ', '.join(conflicts))
    return rows


def install(rootfs, rows):
    live = rootfs.resolve().parent == rootfs.resolve()
    if live:
        if not sys.platform.startswith('linux') or platform.machine() not in ('armv7l', 'armv8l') or os.geteuid() != 0:
            raise ValueError('Live installation requires root on ARMv7 Linux')
        # A functioning system is an upgrade target, never a fresh install.
        if (Path('/run/pynq-shared-fpga/state.json').exists() or
                Path('/home/xilinx/gesture_demo/camera_web/data/current.json').exists()):
            raise ValueError('Existing board session detected; use documented upgrade/backup procedure')
    made = []
    try:
        for row in rows:
            if row['already_matches']:
                continue
            src, dst = Path(row['source_path']), Path(row['destination'])
            if project.sha(src) != row['sha256']:
                raise ValueError('Source changed: ' + str(src))
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive destination creation prevents accidental overwrite races.
            with src.open('rb') as source, dst.open('xb') as output:
                made.append(dst)
                shutil.copyfileobj(source, output)
            dst.chmod(int(row['mode'], 8))
            if project.sha(dst) != row['sha256']:
                raise ValueError('Installed digest mismatch: ' + str(dst))
        for folder in ['home/xilinx/shared_fpga', 'home/xilinx/ch9329_hid/logs',
                       'home/xilinx/gesture_demo/camera_web/data']:
            (rootfs/folder).mkdir(parents=True, exist_ok=True)
        if live:
            import pwd
            user = pwd.getpwnam('xilinx')
            for row in rows:
                dst = Path(row['destination'])
                if row['target'].startswith('/home/xilinx/'):
                    os.chown(dst, user.pw_uid, user.pw_gid)
                    for p in dst.parents:
                        if p == Path('/home/xilinx'):
                            break
                        os.chown(p, user.pw_uid, user.pw_gid)
            for folder in ['shared_fpga', 'ch9329_hid/logs', 'gesture_demo/camera_web/data']:
                os.chown(Path('/home/xilinx')/folder, user.pw_uid, user.pw_gid)
    except BaseException:
        for path in reversed(made):
            path.unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rootfs', type=Path, default=Path('/'))
    p.add_argument('--camera-config', required=True, type=Path)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    rootfs = a.rootfs.resolve()
    rows = plan(rootfs, a.camera_config)
    if a.apply:
        install(rootfs, rows)
    print(json.dumps(dict(files=len(rows), applied=a.apply, rootfs=str(rootfs),
                          services_started=False, files_written=[r['target'] for r in rows if not r['already_matches']],
                          next='Run board/preflight.py, then follow report/reproduction/README.md'), indent=2))


if __name__ == '__main__':
    main()
