"""Stage, verify, back up, and deploy the eye-presence web change over SSH.

Credentials are supplied through PYNQ_SSH_PASSWORD. Run from the workstation.
An active capture/analysis blocks installation. No FPGA, HID, model classifier,
camera-role configuration, or saved inspection data is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import shlex
import time

import paramiko


ROOT = Path(__file__).resolve().parents[1]
WEB = '/home/xilinx/gesture_demo/camera_web'
PYTHON = '/usr/local/share/pynq-venv/bin/python3'
FILES = ['workers.py', 'service.py', 'review.html', 'eye_presence.py',
         'eye_presence_models/haarcascade_eye.xml',
         'eye_presence_models/haarcascade_eye_tree_eyeglasses.xml']


def run(client, command, timeout=60, input_data=None):
    stdin, out, err = client.exec_command(command, timeout=timeout)
    if input_data is not None:
        stdin.write(input_data)
        stdin.flush()
        stdin.channel.shutdown_write()
    stdout, stderr = out.read().decode('utf-8', 'replace'), err.read().decode('utf-8', 'replace')
    if out.channel.recv_exit_status():
        raise RuntimeError(stdout + '\n' + stderr)
    return stdout


INSTALLER = r'''
import hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
b=Path(__file__).resolve().parent
web=Path('/home/xilinx/gesture_demo/camera_web')
manifest=json.loads((b/'manifest.json').read_text())
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def state():
    with urllib.request.urlopen('http://192.168.2.99:8080/api/state',timeout=8) as r:return json.load(r)
def install(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    temp=dst.with_name(dst.name+'.presence-new')
    shutil.copyfile(src,temp);temp.chmod(0o644);temp.replace(dst)
def restart():
    subprocess.run(['systemctl','start','pynq-camera-web'],check=True)
    for _ in range(30):
        try:return state()
        except OSError:time.sleep(.5)
    raise RuntimeError('Web health check timed out')
if os.geteuid()!=0:raise RuntimeError('root required')
before=state()
if before.get('busy'):raise RuntimeError('Capture or analysis active; retry after completion')
for name,row in manifest.items():
    if digest(b/name)!=row['sha256']:raise RuntimeError('Staged checksum mismatch: '+name)
    if row.get('expected_before') and digest(web/name)!=row['expected_before']:
        raise RuntimeError('Production source differs from verified baseline: '+name)
backup=b/'backup';backup.mkdir()
existed=[]
for name in manifest:
    if (web/name).exists():
        dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(web/name,dest);existed.append(name)
(backup/'state.json').write_text(json.dumps(before,indent=2))
fpga=Path('/run/pynq-shared-fpga/state.json').read_bytes()
try:
    subprocess.run(['systemctl','stop','pynq-camera-web'],check=True)
    for name in manifest:install(b/name,web/name)
    after=restart()
    if after['session_id']!=before['session_id']:raise RuntimeError('Current inspection changed')
    if Path('/run/pynq-shared-fpga/state.json').read_bytes()!=fpga:raise RuntimeError('FPGA state changed')
    if 'image_validation' not in after:raise RuntimeError('New API field missing')
    for name,row in manifest.items():
        if digest(web/name)!=row['sha256']:raise RuntimeError('Installed checksum mismatch')
    result=dict(installed=True,before=before,after=after,fpga_unchanged=True,manifest=manifest)
    (b/'deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
except BaseException:
    subprocess.run(['systemctl','stop','pynq-camera-web'],check=True)
    for name in manifest:
        if name in existed:install(backup/name,web/name)
        elif (web/name).is_file():(web/name).unlink()
    restart()
    raise
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.2.99')
    parser.add_argument('--user', default='xilinx')
    parser.add_argument('--fixtures-dir', type=Path, default=Path(os.environ.get('EYE_PRESENCE_FIXTURES', ROOT/'sim/eye_presence/fixtures')))
    args = parser.parse_args()
    for name in ('eye_07.png', 'background_desk.png'):
        if not (args.fixtures_dir/name).is_file():
            parser.error('Missing private smoke fixture: '+str(args.fixtures_dir/name))
    password = os.environ.get('PYNQ_SSH_PASSWORD')
    if not password:
        raise RuntimeError('Set PYNQ_SSH_PASSWORD in the environment')
    baseline = json.loads((ROOT/'board/eye_presence_baseline.json').read_text(encoding='utf-8'))
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(args.host, username=args.user, password=password, timeout=12,
                       look_for_keys=False, allow_agent=False)
        stage = '/home/{}/eye_presence_{}'.format(args.user, time.strftime('%Y%m%d_%H%M%S'))
        run(client, 'mkdir -m 755 ' + shlex.quote(stage))
        sftp = client.open_sftp()
        manifest = {}
        for name in FILES:
            source = ROOT/'src/ps/camera_web'/name
            manifest[name] = dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                                  expected_before=baseline.get(name))
            parent = posixpath.dirname(stage+'/'+name)
            run(client, 'mkdir -p ' + shlex.quote(parent))
            sftp.put(str(source), stage+'/'+name)
        for name, content in [('manifest.json', json.dumps(manifest)), ('install.py', INSTALLER)]:
            with sftp.open(stage+'/'+name, 'w') as f:
                f.write(content.encode('utf-8'))
        # The staged native detector must work on real board OpenCV before restart.
        sftp.put(str(args.fixtures_dir/'eye_07.png'), stage+'/eye.png')
        sftp.put(str(args.fixtures_dir/'background_desk.png'), stage+'/background.png')
        smoke = "import sys;sys.path.insert(0,{0!r});from eye_presence import detect_eye;assert detect_eye({1!r})['detected'];assert not detect_eye({2!r})['detected'];print('Board detector smoke passed')".format(stage, stage+'/eye.png', stage+'/background.png')
        # Match the service's OpenCV overrides; a plain SSH Python process can
        # otherwise pass while the real spawned workflow lacks objdetect.
        settings = shlex.split(run(client, 'systemctl show -p Environment --value pynq-camera-web'))
        overrides = [entry for entry in settings
                     if entry.split('=', 1)[0] in ('PYTHONPATH', 'LD_LIBRARY_PATH')]
        runtime = 'env ' + ' '.join(shlex.quote(entry) for entry in overrides) + ' ' + PYTHON
        print(run(client, runtime+' -c '+shlex.quote(smoke)))
        for name in ('workers.py', 'service.py', 'eye_presence.py'):
            print(run(client, PYTHON+' -m py_compile '+shlex.quote(stage+'/'+name)))
        result = run(client, "sudo -S -p '' "+PYTHON+' '+shlex.quote(stage+'/install.py'),
                     timeout=120, input_data=password+'\n')
        evidence = ROOT/'sim/results/eye_presence_deployment.json'
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(result, encoding='utf-8')
        print('Installed. Board backup and evidence: '+stage)
        print(result)
    finally:
        client.close()


if __name__ == '__main__':
    main()
