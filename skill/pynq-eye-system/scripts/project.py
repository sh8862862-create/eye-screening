"""Unified project operations. No implicit board mutation or credential storage."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from urllib.parse import urlparse
from urllib.request import urlopen

SKILL = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def project_root(explicit=None):
    selected = explicit or os.environ.get('EYE_PROJECT_ROOT')
    candidates = [Path(selected)] if selected else list(SKILL.parents)
    for candidate in candidates:
        root = candidate.resolve()
        if (root / 'resources/manifest.json').is_file() and (root / 'src/ps/shared/shared_fpga.py').is_file():
            return root
    raise ValueError('Project not found. Use --project-root or EYE_PROJECT_ROOT pointing to eye_screening.')


def confined(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Path outside project: ' + str(relative))
    return path


def resolve_original(root, original, source_root='workspace'):
    root = full_package_root()
    inventory = read_json(root / 'report/organization/file_inventory.json')
    rows = [r for r in inventory['files'] if r['root'] == source_root and r['original'] == original]
    if len(rows) != 1:
        raise ValueError('Source not uniquely present in this edition: ' + original)
    path = confined(root, rows[0]['path'])
    if not path.is_file() or sha(path) != rows[0]['sha256']:
        raise ValueError('Archived source missing or changed: ' + original)
    return path


def full_package_root():
    selected = os.environ.get('EYE_FULL_PACKAGE_ROOT')
    if not selected:
        raise ValueError('Historical operation needs EYE_FULL_PACKAGE_ROOT; see docs/resources.md')
    root = Path(selected).resolve()
    if not (root / 'report/organization/file_inventory.json').is_file():
        raise ValueError('Full package inventory not found')
    return root


def require_resources(root, names):
    manifest = read_json(root / 'resources/manifest.json')
    expected = {row['path']: row for row in manifest['files']}
    for name in names:
        path = confined(root, name)
        if not path.is_file() or (name in expected and sha(path) != expected[name]['sha256']):
            raise ValueError('Missing/changed resource: ' + name + '; import resources using tools/resources.py')


def catalog(root):
    rows = read_json(SKILL / 'assets/capabilities.json')['capabilities']
    external = {r['path'] for r in read_json(root / 'resources/manifest.json')['files']}
    result = []
    for row in rows:
        item = dict(row)
        missing = [p for p in row['resources'] if not confined(root, p).is_file()]
        item['missing_resources'] = [p for p in missing if p not in external]
        item['optional_resources_missing'] = [p for p in missing if p in external]
        item['runtime_ready'] = not missing and not row.get('archive_resources')
        item['reference_exists'] = (SKILL / row['reference']).is_file()
        result.append(item)
    return result


def verify_resources(root):
    errors = []
    for entry in read_json(SKILL / 'assets/resource_manifest.json')['files']:
        path = confined(root, entry['path'])
        if not path.is_file() or path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            errors.append(entry['path'])
    return errors


def release_plan(root):
    mapping = read_json(SKILL / 'assets/deployment_map.json')
    trust = read_json(SKILL / 'assets/resource_manifest.json')
    expected = {r['path']: r['sha256'] for r in trust['files']}
    targets = set()
    result = []
    for row in mapping['files']:
        path = confined(root, row['source'])
        target = row['target']
        if not target.startswith(('/opt/', '/home/xilinx/', '/etc/systemd/')) or '..' in target.split('/'):
            raise ValueError('Invalid board target: ' + target)
        if target in targets:
            raise ValueError('Duplicate target: ' + target)
        targets.add(target)
        require_resources(root, [row['source']])
        actual = sha(path)
        if actual != expected.get(row['source']):
            raise ValueError('Release source differs from selected baseline: ' + row['source'])
        result.append({**row, 'sha256': actual, 'bytes': path.stat().st_size})
    return {'schema': 1, 'kind': 'reviewable_payload_not_installer', 'board': 'PYNQ-Z2',
            'files': result, 'preconditions': mapping['preconditions'],
            'preserve': mapping['preserve'], 'postchecks': mapping['postchecks']}


def pack_release(root, destination):
    plan = release_plan(root)
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Output already exists: ' + str(destination))
    if not destination.parent.is_dir():
        raise ValueError('Output parent must exist')
    with destination.open('xb') as output:
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            payload = json.dumps(plan, ensure_ascii=False, indent=2).encode('utf-8')
            info = tarfile.TarInfo('deployment_manifest.json')
            info.size, info.mode = len(payload), 0o644
            archive.addfile(info, io.BytesIO(payload))
            for row in plan['files']:
                source = root / row['source']
                info = archive.gettarinfo(str(source), arcname='rootfs/' + row['target'].lstrip('/'))
                info.mode = int(row['mode'], 8)
                # Ownership is assigned by the installer, not the workstation user.
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                with source.open('rb') as incoming:
                    archive.addfile(info, incoming)
    return {'output': str(destination), 'files': len(plan['files']), 'sha256': sha(destination),
            'installed': False}


def collect_snapshot(base_url, timeout=4):
    parsed = urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Use an http(s) base URL without credentials')
    if parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Use the service origin, without path/query/fragment')
    result = {'url': base_url.rstrip('/'), 'read_only': True, 'observations': {}, 'errors': {}}
    for name in ('state', 'devices'):
        try:
            with urlopen(result['url'] + '/api/' + name, timeout=timeout) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError('Response exceeds 2 MiB')
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError('Expected a JSON object')
            result['observations'][name] = value
        except Exception as exc:
            result['errors'][name] = str(exc)
    return result


def diagnose(snapshot):
    observations = snapshot.get('observations', {})
    state = observations.get('state', snapshot if 'phase' in snapshot else {})
    findings = []
    if snapshot.get('errors'):
        findings.append({'issue': '服务状态未完整取得', 'next': '检查板卡电源、网线、地址和服务监听；超时不能证明板卡掉电。'})
    if not state:
        findings.append({'issue': '缺少状态证据', 'next': '采集 /api/state，勿据缺失数据宣布运行正常。'})
    if state.get('camera_active') and state.get('frame_fresh') is False:
        findings.append({'issue': '相机运行但帧不新鲜', 'next': '按 camera_workflow.md 核对 USB 枚举、角色、设备占用及采集日志。'})
    if state.get('inference_active') and state.get('result_stale') is True:
        findings.append({'issue': '推理结果已过期', 'next': '区分 DETECT/TRACK，测量裁剪、网络、IPC 和并发采集负载。'})
    if state.get('gesture_output_error'):
        findings.append({'issue': '控制输出报错', 'next': '按 gesture_hid.md 检查报告门控、CH9329 UART、USB connected 和输出新鲜度。'})
    if state.get('error'):
        findings.append({'issue': '业务状态报错', 'detail': state['error'], 'next': '保留当前检查号和日志，按 deployment_recovery.md 限定修订范围。'})
    if not findings:
        findings.append({'issue': '有限状态快照未触发诊断条件', 'next': '仍需按 acceptance.md 进行实物闭环验证；快照不证明所有模块正常。'})
    return {'findings': findings, 'mutations': 0}


def run(argv, cwd):
    return subprocess.call([str(a) for a in argv], cwd=cwd, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--python', default=sys.executable, help='Interpreter with required project dependencies')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('capabilities')
    commands.add_parser('verify')
    commands.add_parser('test')
    locate = commands.add_parser('locate')
    locate.add_argument('original')
    locate.add_argument('--source-root', default='workspace')
    restore = commands.add_parser('restore')
    restore.add_argument('--source-root', default='workspace')
    restore.add_argument('--destination', type=Path, required=True)
    release = commands.add_parser('release')
    release.add_argument('--output', type=Path, help='Omit for a read-only deployment plan')
    snapshot = commands.add_parser('snapshot')
    snapshot.add_argument('--url', required=True)
    snapshot.add_argument('--output', type=Path)
    diag = commands.add_parser('diagnose')
    diag.add_argument('--snapshot', type=Path, required=True)
    build = commands.add_parser('build')
    build.add_argument('target', choices=('hls', 'overlay', 'ncnn'))
    build.add_argument('--execute', action='store_true', help='Execute the displayed build using installed tools')
    infer = commands.add_parser('infer')
    infer.add_argument('--image', required=True, type=Path)
    infer.add_argument('--output-dir', required=True, type=Path)
    infer.add_argument('--localize', action='store_true')
    report = commands.add_parser('report')
    report.add_argument('--result', required=True, type=Path)
    report.add_argument('--image', required=True, type=Path)
    report.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == 'snapshot':
        if args.output and args.output.exists():
            raise ValueError('Output exists')
        result = collect_snapshot(args.url)
        if args.output:
            with args.output.open('x', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        emit(result)
        return int(bool(result['errors']))
    if args.command == 'diagnose':
        emit(diagnose(read_json(args.snapshot)))
        return 0
    root = project_root(args.project_root)
    if args.command == 'capabilities':
        rows = catalog(root)
        emit({'project_root': str(root), 'capabilities': rows})
        return int(any(r['missing_resources'] or not r['reference_exists'] for r in rows))
    if args.command in ('verify', 'test'):
        script = 'verify_package.py' if args.command == 'verify' else 'run_gesture_tests.py'
        return run([args.python, '-B', root / 'sim' / script], root)
    if args.command == 'locate':
        emit({'path': str(resolve_original(root, args.original, args.source_root))})
    elif args.command == 'restore':
        full = full_package_root()
        return run([args.python, '-B', full / 'build/scripts/restore_original_layout.py', '--root', args.source_root,
                    '--destination', args.destination.resolve()], full)
    elif args.command == 'release':
        emit(pack_release(root, args.output) if args.output else release_plan(root))
    elif args.command == 'build':
        builds = {'hls': ['vitis_hls', '-f', 'build/scripts/build_palm_hls.tcl'],
                  'overlay': ['vivado', '-mode', 'batch', '-source', 'build/scripts/build_palm_overlay.tcl'],
                  'ncnn': ['sh', 'build/scripts/build_ncnn.sh']}
        emit({'cwd': str(root), 'argv': builds[args.target], 'execute': args.execute})
        if args.execute:
            if args.target == 'ncnn':
                require_resources(root, ['data/third_party/ncnn-20261020.zip'])
            return run(builds[args.target], root)
    elif args.command == 'infer':
        image = args.image.resolve()
        if not image.is_file():
            raise ValueError('Input image does not exist')
        output = args.output_dir.resolve()
        if output.exists():
            raise ValueError('Output directory must not exist')
        models = root / 'data/models/eye'
        required = ['data/models/eye/mobilenet_v3_small.onnx']
        if args.localize:
            required.append('data/models/eye/mobile_unet.onnx')
        require_resources(root, required)
        output.mkdir(parents=True)
        command = [args.python, '-B', root / 'src/ps/eye/infer.py', '--image', image,
                   '--classifier', models / 'mobilenet_v3_small.onnx', '--metadata', models / 'mobilenet_v3_small.json',
                   '--json', output / 'result.json', '--overlay', output / 'overlay.png', '--report', output / 'report.html']
        if args.localize:
            command += ['--localizer', models / 'mobile_unet.onnx']
        return run(command, root)
    elif args.command == 'report':
        if args.output.exists():
            raise ValueError('Output exists')
        if not args.result.is_file() or not args.image.is_file():
            raise ValueError('Result and image must exist')
        return run([args.python, '-B', root / 'src/ps/eye/report.py', '--result', args.result.resolve(),
                    '--image', args.image.resolve(), '--html', args.output.resolve()], root)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('Project operation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
