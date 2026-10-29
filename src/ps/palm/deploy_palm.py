"""Install a measured candidate with an explicit backup and automatic rollback."""
import hashlib,json,os,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
b=Path(__file__).resolve().parent
target=Path('/opt/pynq-palm')
backup=b/'production_backup'
consumers=['pynq-camera-web','pynq-buttons','ch9329-hid','eye-oled']
owner=Path('/opt/pynq-shared/shared_fpga.py')
worker=Path('/home/xilinx/gesture_demo/camera_web/workers.py')
unit=Path('/etc/systemd/system/pynq-palm.service')
fpga_override=Path('/etc/systemd/system/pynq-fpga.service.d/palm-accel.conf')
web_override=Path('/etc/systemd/system/pynq-camera-web.service.d/palm-accel.conf')

def run(*args):
    print('RUN',*args,flush=True);subprocess.run(args,check=True)
def active(name):return subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0
def state():
    with urllib.request.urlopen('http://192.168.2.99:8080/api/state',timeout=8) as r:return json.load(r)
def install(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    temporary=dest.with_name(dest.name+'.palm-new')
    shutil.copyfile(source,temporary);temporary.chmod(0o644);temporary.replace(dest)
def restore():
    run('systemctl','stop','pynq-palm',*consumers)
    run('systemctl','stop','pynq-fpga')
    run('systemctl','disable','pynq-palm')
    install(backup/'shared_fpga.py',owner);install(backup/'workers.py',worker)
    if (backup/'index.html').exists():install(backup/'index.html',worker.with_name('index.html'))
    for path in [unit,fpga_override,web_override]:path.unlink(missing_ok=True)
    run('systemctl','daemon-reload');run('systemctl','start','pynq-fpga')
    previous=json.loads((backup/'state.json').read_text())
    if previous['active']:run('systemctl','start',*previous['active'])
    print('PRODUCTION_ROLLED_BACK',flush=True)

if os.geteuid()!=0:raise RuntimeError('root required')
if '--rollback' in sys.argv:
    restore();raise SystemExit(0)
if backup.exists() or target.exists() or any(p.exists() for p in [unit,fpga_override,web_override]):
    raise RuntimeError('existing deployment; inspect before replacing')
current=state()
if current.get('busy') or current.get('camera_active') or current.get('inference_active'):
    raise RuntimeError('camera or inference active; leave current session untouched')
expected=json.loads((b/'expected_before.json').read_text())
for path in [owner,worker]:
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected[str(path)]:
        raise RuntimeError('production changed since preparation: '+str(path))
required=['shared_fpga.py','workers.py','pynq-palm.service','pynq_shared.bit','pynq_shared.hwh',
          'palm_custom.onnx','weights.npz','pl_backend.py','custom_layer.py','palm_service.py',
          'palm_rpc.py','native_layer.py','libpalm_layer.so']
for name in required:
    if not (b/name).is_file():raise FileNotFoundError(name)
sys.path.insert(0,str(b));from shared_fpga import hardware_metadata
hardware_metadata(b/'pynq_shared.bit')
backup.mkdir()
shutil.copy2(owner,backup/'shared_fpga.py');shutil.copy2(worker,backup/'workers.py')
was_active=[s for s in consumers if active(s)]
(backup/'state.json').write_text(json.dumps(dict(active=was_active,web=current,
    fpga=json.loads(Path('/run/pynq-shared-fpga/state.json').read_text())),indent=2))
try:
    run('systemctl','stop',*consumers);run('systemctl','stop','pynq-fpga')
    target.mkdir(mode=0o755)
    for name in required:
        if name not in ['shared_fpga.py','workers.py','pynq-palm.service']:install(b/name,target/name)
    install(b/'shared_fpga.py',owner);install(b/'workers.py',worker);install(b/'pynq-palm.service',unit)
    fpga_override.parent.mkdir(parents=True,exist_ok=True)
    fpga_override.write_text('[Service]\nExecStart=\nExecStart=/usr/local/share/pynq-venv/bin/python3 /opt/pynq-shared/shared_fpga.py load --bitfile /opt/pynq-palm/pynq_shared.bit\n')
    web_override.parent.mkdir(parents=True,exist_ok=True)
    web_override.write_text('[Unit]\nWants=pynq-palm.service\nAfter=pynq-palm.service\n[Service]\nEnvironment=PYNQ_PALM_PL=1\n')
    run('systemctl','daemon-reload');run('systemctl','start','pynq-fpga','pynq-palm')
    if was_active:run('systemctl','start',*was_active)
    run('systemctl','is-active','pynq-fpga','pynq-palm',*was_active)
    # Exercise the exact public path as the unprivileged production user.
    run('runuser','-u','xilinx','--','env','PYTHONPATH=/opt/pynq-palm',
        sys.executable,'-c','import numpy as np; from palm_rpc import predict; print([x.shape for x in predict(np.zeros((1,192,192,3),np.float32))])')
    current=state()
    if current.get('camera_active') or current.get('inference_active'):
        raise RuntimeError('unexpected camera startup after install')
    run('systemctl','enable','pynq-palm')
    (b/'deployment_verified.json').write_text(json.dumps(dict(web=current,
        fpga=json.loads(Path('/run/pynq-shared-fpga/state.json').read_text())),indent=2))
    print('PALM_DEPLOYED_AND_VERIFIED',flush=True)
except BaseException:
    restore()
    raise
