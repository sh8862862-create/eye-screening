"""Install validated pose software while idle; do not reload FPGA or touch HID."""
import hashlib,json,os,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
b=Path(__file__).resolve().parent
backup=b/'production_backup'
target=Path('/opt/pynq-gesture')
worker=Path('/home/xilinx/gesture_demo/camera_web/workers.py')
control=Path('/home/xilinx/gesture_demo/gesture_image_test/gesture_control.py')
override=Path('/etc/systemd/system/pynq-camera-web.service.d/pose-latency.conf')
def run(*args):subprocess.run(args,check=True)
def state():
    with urllib.request.urlopen('http://192.168.2.99:8080/api/state',timeout=8) as r:return json.load(r)
def install(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    temporary=dest.with_name(dest.name+'.latency-new')
    shutil.copyfile(source,temporary);temporary.chmod(0o644);temporary.replace(dest)
def restore():
    run('systemctl','stop','pynq-camera-web')
    install(backup/'workers.py',worker);install(backup/'gesture_control.py',control)
    override.unlink(missing_ok=True)
    run('systemctl','daemon-reload');run('systemctl','start','pynq-camera-web')
    print('LATENCY_ROLLED_BACK',flush=True)
if os.geteuid()!=0:raise RuntimeError('root required')
current=state()
if any(current.get(k) for k in ('busy','camera_active','inference_active')):
    raise RuntimeError('Session active; defer deployment until idle')
if '--rollback' in sys.argv:restore();raise SystemExit(0)
if backup.exists() or target.exists() or override.exists():raise RuntimeError('Existing deployment; inspect before replacing')
expected=json.loads((b/'expected_before.json').read_text())
for p in (worker,control):
    if hashlib.sha256(p.read_bytes()).hexdigest()!=expected[p.name]:raise RuntimeError('Production changed: '+str(p))
required=['fast_handpose.py','pose_runtime.py','ncnn_runtime.py','libgesture_ncnn.so','handpose.ncnn.param','handpose.ncnn.bin','NCNN_LICENSE.txt']
manifest=json.loads((b/'install_manifest.json').read_text())
for name in required+['workers.py','gesture_control.py']:
    if hashlib.sha256((b/name).read_bytes()).hexdigest()!=manifest[name]:raise RuntimeError('Invalid staged artifact: '+name)
backup.mkdir()
for p in (worker,control):shutil.copy2(p,backup/p.name)
(backup/'state.json').write_text(json.dumps(current,indent=2))
try:
    run('systemctl','stop','pynq-camera-web')
    target.mkdir(mode=0o755)
    for name in required:install(b/name,target/name)
    install(b/'workers.py',worker);install(b/'gesture_control.py',control)
    override.parent.mkdir(parents=True,exist_ok=True)
    override.write_text('[Service]\nEnvironment=PYNQ_POSE_FAST=1\nEnvironment=PYNQ_POSE_ENGINE=ncnn\n')
    run('systemctl','daemon-reload');run('systemctl','start','pynq-camera-web')
    final=None
    for attempt in range(20):
        try:final=state();break
        except OSError:time.sleep(.5)
    if final is None or final.get('error'):raise RuntimeError('Web health verification failed')
    run('systemctl','is-active','--quiet','pynq-camera-web')
    result=dict(web=final,hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [worker,control,*[target/n for n in required]]})
    (b/'deployment.json').write_text(json.dumps(result,indent=2))
    print('LATENCY_DEPLOYED',flush=True)
except BaseException:
    restore();raise
