"""LAN-only PYNQ camera workbench. Camera lifecycle is independent of HTTP."""
import argparse
from collections import deque
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing as mp
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from workers import camera_worker, inference_worker, workflow_worker
from device_status import DeviceStatus
from gesture_output import read_output_mode

BASE = Path(__file__).resolve().parent
READY = ('RESULT_READY', 'GESTURE_TEST_FINISHED')
ACTIONS = {'start', 'stop', 'eye', 'gesture', 'infer_on', 'infer_off', 'capture', 'analyze', 'review', 'end_session'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, data):
    temp = Path(path).with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_report(record):
    if record.get('state') not in READY:
        raise ValueError('本次眼部报告尚未就绪，请先拍照并完成处理。')
    if sha(record['image']) != record['image_sha256']:
        raise ValueError('原始照片校验失败，禁止进入阅片。')
    for key in ('result', 'report', 'overlay'):
        if sha(record['analysis'][key]) != record['analysis'][key+'_sha256']:
            raise ValueError('报告完整性校验失败：' + key)


class Manager:
    def __init__(self, root, data, start_thread=True, clock=time.monotonic):
        self.root, self.data = Path(root), Path(data)
        self.switch = self.root / 'camera_switch'
        self.config = read_json(self.switch / 'camera_roles.json')
        if self.config.get('roles_confirmed') is not True:
            raise ValueError('摄像头用途尚未确认。')
        self.data.mkdir(parents=True, exist_ok=True)
        self.clock, self.mu = clock, threading.RLock()
        self.hid_serial_device = os.environ.get('PYNQ_HID_SERIAL', '').strip() or None
        self.hid_serial_baud = int(os.environ.get('PYNQ_HID_SERIAL_BAUD', '115200'))
        self.ctx = mp.get_context('spawn')
        self.commands = queue.Queue(maxsize=1)
        self.quit = threading.Event()
        self.thread = None
        self.proc = self.infer = self.job = None
        self.infer_serial_device = None
        self.lock_file = None
        # Fresh sessions begin at B so the first explicit workflow action is
        # eye capture.  A restored ready report resumes at A below.
        self.role = 'eye'
        self.phase, self.error = 'IDLE', ''
        self.busy, self.auto = False, False
        self.last_seen = 0.
        self.started_at = self.clock()
        self.last_tick = self.clock()
        self.frame = self.result_image = None
        self.frame_at = self.result_at = 0.
        self.frame_seq = self.result_seq = 0
        self.preview_rotation = 0
        self.preview_transport = 'jpeg'
        self.record = None
        self.result = {}
        self.times = deque(maxlen=30)
        self.session = None
        current = self.data / 'current.json'
        stopped = (self.data / 'inspection_stopped.json').exists()
        self.stop_marker_at_start = stopped
        if stopped:
            # BTN2 cleared only the active pointer; keep all historical files.
            atomic_json(current, dict(session=None))
        if current.is_file() and not stopped:
            try:
                saved = read_json(current).get('session')
                if saved:
                    session = (self.data / saved).resolve()
                    if session.parent != self.data.resolve():
                        raise ValueError('Invalid saved session path')
                    self.record = read_json(session / 'session.json')
                    self.session = session
                    # Never restart an unfinished medical workflow automatically.
                    if self.record.get('state') not in READY:
                        self.auto = False
                        self.role = 'eye'
                    elif self.record.get('analysis'):
                        verify_report(self.record)
                        self.role = 'gesture'
                        self.auto = True
            except Exception as exc:
                self.error = '上次会话未能恢复：' + str(exc)
                self.auto = False
        if start_thread:
            self.thread = threading.Thread(target=self._loop, daemon=True, name='camera-supervisor')
            self.thread.start()

    def control_output_state(self):
        with self.mu:
            configured, error = read_output_mode(self.data / 'gesture_output.json', self.hid_serial_device)
            mode = configured
            # A running legacy serial worker owns its output until stopped.  Do
            # not activate a second route while that worker can still emit HID.
            if self.infer is not None:
                if self.infer_serial_device:
                    mode = 'serial_hid'
                elif configured == 'serial_hid':
                    mode = 'disabled'
            return dict(gesture_output=mode, gesture_output_configured=configured,
                        gesture_output_pending=mode != configured,
                        gesture_output_error=error,
                        control_outputs=mode in ('pl_uart', 'serial_hid'),
                        hid_serial_enabled=mode == 'serial_hid')

    def snapshot(self):
        with self.mu:
            now = self.clock()
            fresh = self.frame is not None and now-self.frame_at < 2
            result_age = now-self.result_at if self.result_at else None
            return dict(phase=self.phase, role=self.role, busy=self.busy, error=self.error,
                        camera_active=self.proc is not None, frame_fresh=fresh,
                        frame_seq=self.frame_seq, frame_age_s=round(now-self.frame_at, 2) if self.frame_at else None,
                        preview_fps=round((len(self.times)-1)/(self.times[-1]-self.times[0]),1) if len(self.times)>1 else 0,
                        preview_rotation=self.preview_rotation, preview_transport=self.preview_transport,
                        inference_active=self.infer is not None, result=self.result,
                        result_seq=self.result_seq, result_age_s=round(result_age,2) if result_age is not None else None,
                        result_stale=result_age is None or result_age > 6,
                        **self.control_output_state(),
                        session_id=(self.record or {}).get('check_id'),
                        workflow_state=(self.record or {}).get('state'),
                        image_validation=(self.record or {}).get('image_validation'),
                        report_ready=(self.record or {}).get('state') in READY,
                        auto_resume=self.auto, uptime_s=round(now-self.started_at),
                        free_mb=round(shutil.disk_usage(self.data).free/1024**2))

    def lease(self):
        with self.mu:
            self.last_seen = self.clock()
            if self.auto and self.proc is None and self.job is None and not self.busy and not self.error:
                self.action('start')
        return self.snapshot()

    def action(self, name, payload=None):
        if name not in ACTIONS:
            raise ValueError('未知操作。')
        payload = payload or {}
        with self.mu:
            if self.busy:
                raise ValueError('上一项操作尚未完成，请等待。')
            if name == 'analyze' and payload.get('confirm_eye') is not True:
                raise ValueError('请先检查照片，并明确确认这是可用的眼部研究测试图。')
            if name in ('analyze', 'review') and self.record is None:
                raise ValueError('请先完成本次拍照。')
            self.busy = True
            try:
                self.commands.put_nowait((name, payload))
            except queue.Full:
                self.busy = False
                raise ValueError('操作队列正忙。')
        return self.snapshot()

    def _acquire(self):
        import fcntl
        if self.lock_file is not None:
            return
        handle = (self.switch / 'sequential_camera.lock').open('a+')
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            devices = []
            missing = []
            for role, label, port in (('eye', '眼部', '1.4'), ('gesture', '手势', '1.3')):
                try:
                    devices.append(str(Path(self.config[role]).resolve(strict=True)))
                except FileNotFoundError:
                    missing.append('%s相机（预期 USB %s）' % (label, port))
            if missing:
                raise ValueError('未检测到' + '、'.join(missing) + '。请核对设备面板及 HUB 上行数据连接。')
            if devices[0] == devices[1]:
                raise ValueError('两种用途解析到了同一摄像头，禁止启动。')
            # Cooperative lock alone does not cover old notebooks or camera_preview.py.
            check = subprocess.run(['/usr/bin/fuser', *devices], capture_output=True, timeout=3)
            if check.returncode == 0:
                raise RuntimeError('摄像头被其他程序占用，请先停止原预览或识别程序。')
            if check.returncode != 1:
                raise RuntimeError('无法核验摄像头占用，已停止。')
            self.lock_file = handle
        except BaseException:
            handle.close()
            raise

    def _unlock(self):
        if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None

    @staticmethod
    def _reap(process, event=None):
        if process is None:
            return False
        if event is not None:
            event.set()
        process.join(3)
        forced = process.is_alive()
        if forced:
            process.terminate()
            process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
        if process.is_alive():
            raise RuntimeError('采集进程未退出，禁止切换相机。')
        return forced

    def _stop_infer(self):
        if self.infer is not None:
            self._reap(self.infer, self.infer_stop)
            self.infer = None
            self.infer_q.close()
        self.infer_serial_device = None
        with self.mu:
            self.result, self.result_image, self.result_at = {}, None, 0.

    def _stop_camera(self, strict=True):
        # Stop capture before waiting for/terminating a model pass. Otherwise
        # the producer can block on a lock held by a forcibly stopped consumer.
        if self.proc is not None:
            self.cap_stop.set()
        self._stop_infer()
        bad = False
        if self.proc is not None:
            forced = self._reap(self.proc, self.cap_stop)
            logging.info('camera_stop forced=%s released=%s exitcode=%s', forced, self.released.value, self.proc.exitcode)
            bad = forced or self.released.value != 1 or self.proc.exitcode != 0
            self.proc = None
            self.cap_q.close()
        with self.mu:
            self.frame, self.frame_at = None, 0.
            self.preview_rotation, self.preview_transport = 0, 'jpeg'
            self.times.clear()
            self.phase = 'IDLE'
        self._unlock()
        if bad and strict:
            raise RuntimeError('摄像头未正常释放，已终止本次操作；检查接线后再手动重试。')

    def _start(self, role, explicit=False):
        marker = self.data / 'inspection_stopped.json'
        if marker.exists():
            # A worker finishing in the old process must not undo BTN2.
            if not explicit or not self.stop_marker_at_start:
                raise ValueError('检查已停止，请等待待机后重新开始。')
            marker.unlink()
            self.stop_marker_at_start = False
        if self.proc is not None and self.role == role:
            return
        self._stop_camera()
        self._acquire()
        self.raw = self.ctx.Array('B', 640*480*3, lock=False)
        self.frame_lock = self.ctx.Lock()
        self.stamp = self.ctx.Value('d', 0, lock=False)
        self.jpeg_size = self.ctx.Value('i', 0, lock=False)
        self.seq = self.ctx.Value('Q', 0, lock=False)
        self.released = self.ctx.Value('i', 0, lock=False)
        self.cap_stop, self.cap_q = self.ctx.Event(), self.ctx.Queue(maxsize=2)
        rotation = self.config.get('gesture_rotation',180) if role == 'gesture' else 0
        self.proc = self.ctx.Process(target=camera_worker,
            args=(self.config[role], rotation, self.raw, self.frame_lock, self.stamp,
                  self.seq, self.released, self.cap_stop, self.cap_q, role == 'gesture', self.jpeg_size), name='camera-'+role)
        self.proc.start()
        with self.mu:
            self.role, self.phase, self.error, self.auto = role, 'OPENING', '', True
            self.opened_at = self.last_seen = self.clock()
        logging.info('camera_start role=%s pid=%s', role, self.proc.pid)

    def _infer_on(self):
        if self.role != 'gesture' or self.proc is None:
            raise ValueError('请先打开 A 手势摄像头。')
        if self.infer is not None:
            return
        self.infer_stop, self.infer_q = self.ctx.Event(), self.ctx.Queue(maxsize=2)
        self.infer_started = self.clock()
        mode, _ = read_output_mode(self.data / 'gesture_output.json', self.hid_serial_device)
        self.infer_serial_device = self.hid_serial_device if mode == 'serial_hid' else None
        self.infer = self.ctx.Process(target=inference_worker,
            args=(str(self.root), self.raw, self.frame_lock, self.stamp, self.seq,
                  self.infer_stop, self.infer_q, self.infer_serial_device,
                  self.hid_serial_baud, self.jpeg_size, self.config.get('gesture_rotation', 180)), name='gesture-inference')
        self.infer.start()

    def _begin_job(self, kind):
        if kind == 'capture' and self.role != 'eye':
            raise ValueError('请先切换到 B 眼部预览，再拍照。')
        if kind == 'analyze' and (self.record or {}).get('state') != 'AWAITING_IMAGE_REVIEW':
            raise ValueError('当前没有等待人工确认的眼部照片。')
        if shutil.disk_usage(self.data).free < 256*1024**2:
            raise ValueError('存储剩余不足 256 MB，请先备份并整理数据。')
        self._stop_camera()
        self._acquire()
        if kind == 'capture':
            self.session = self.data / ('web_session_'+uuid.uuid4().hex)
            self.record = None
        self.job_q = self.ctx.Queue(maxsize=1)
        self.job = self.ctx.Process(target=workflow_worker,
            args=(str(self.switch), str(self.session), self.config, kind, self.job_q), name='eye-'+kind)
        self.job.start()
        self.job_kind, self.job_started = kind, self.clock()
        with self.mu:
            self.auto = False
            self.phase = 'CAPTURING' if kind == 'capture' else 'PROCESSING'
        atomic_json(self.data/'current.json', dict(session=self.session.name))

    def _operate(self, name, payload):
        logging.info('action=%s', name)
        if name == 'start':
            self._start(self.role, explicit=True)
        elif name in ('eye','gesture'):
            if name == 'gesture' and self.record and self.record.get('state') not in READY:
                raise ValueError('眼部会话尚未完成；请先检查照片并处理，或停止本次会话后重新开始。')
            self._start(name, explicit=True)
        elif name == 'stop':
            self.auto = False
            self._stop_camera()
            self.error = ''
        elif name == 'infer_on':
            self._infer_on()
        elif name == 'infer_off':
            self._stop_infer()
        elif name in ('capture','analyze'):
            self._begin_job(name)
        elif name == 'review':
            verify_report(self.record)
            self._start('gesture', explicit=True)
            self._infer_on()
        elif name == 'end_session':
            self._stop_camera()
            self.record, self.session, self.role, self.auto = None, None, 'eye', False
            self.error = ''
            atomic_json(self.data/'current.json', dict(session=None))

    def _fault(self, message):
        logging.error('%s', message)
        try:
            self._stop_camera(strict=False)
        except Exception as exc:
            message += ' / 清理失败：' + str(exc)
        with self.mu:
            self.error, self.phase, self.auto = message, 'ERROR', False

    @staticmethod
    def _drain(q):
        items = []
        while True:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                return items

    def _tick(self):
        now = self.clock()
        if self.proc is not None:
            for item in self._drain(self.cap_q):
                if item['kind'] == 'error':
                    self._fault(item['error'])
                    return
                with self.mu:
                    self.frame, self.frame_at, self.frame_seq = item['image'], item['at'], item['seq']
                    self.preview_rotation = item.get('rotation', 0)
                    self.preview_transport = item.get('transport', 'jpeg')
                    self.times.append(item['at'])
                    self.phase = 'PREVIEW'
            if not self.proc.is_alive():
                self._fault('摄像头进程意外退出，已停止。')
                return
            if now - (self.frame_at or self.opened_at) > (4 if self.frame_at else 15):
                self._fault('摄像头长时间没有新画面，已安全停止，请检查连接后重试。')
                return
            if self.last_seen and now-self.last_seen > 30:
                self._stop_camera()
                logging.info('camera_idle_release')
                return
        if self.infer is not None:
            for item in self._drain(self.infer_q):
                if item['kind'] == 'error':
                    self._stop_infer()
                    self.error = item['error']
                    return
                with self.mu:
                    self.result_image = item.pop('image',None)
                    self.result_at, self.result_seq = item['at'], self.result_seq+1
                    self.result = item
                logging.info('gesture %s', json.dumps(item, ensure_ascii=False))
            if not self.infer.is_alive() or now-max(self.infer_started, self.result.get('completed',0)) > 30:
                self._stop_infer()
                self.error = '识别进程退出或超时；画面预览保留，可手动重新开启识别。'
        if self.job is not None:
            items = self._drain(self.job_q)
            if items:
                outcome = items[-1]
                self.job.join(3)
                if self.job.is_alive():
                    raise RuntimeError('眼部处理退出超时。')
                if outcome.get('ok') and self.job.exitcode == 0:
                    self.record = outcome['record']
                    self.phase = self.record['state']
                    self.error = self.record.get('image_validation', {}).get('message', '')
                    # Analysis finished with both cameras closed.  Only after
                    # the report/hash gate succeeds do we open A and start
                    # the web-only gesture worker.  This keeps the requested
                    # B -> closed -> analysis -> A sequence automatic while
                    # still preventing an unverified report from entering
                    # the review stage.
                    open_gesture = self.record.get('state') == 'RESULT_READY'
                else:
                    open_gesture = False
                    self.phase, self.error = 'ERROR', outcome.get('error','眼部步骤失败')
                    if (self.session/'session.json').is_file():
                        self.record = read_json(self.session/'session.json')
                self.job_q.close()
                self.job = None
                self._unlock()
                self.busy = False
                if open_gesture:
                    verify_report(self.record)
                    self._start('gesture')
                    self._infer_on()
            elif not self.job.is_alive() or now-self.job_started > (55 if self.job_kind=='capture' else 140):
                # Fail the whole service cgroup: nested analysis/capture children must also be reaped.
                logging.critical('workflow_worker_timeout_or_exit')
                os._exit(2)

    def _loop(self):
        while not self.quit.wait(.005):
            self.last_tick = self.clock()
            try:
                try:
                    name, payload = self.commands.get_nowait()
                except queue.Empty:
                    name = None
                if name:
                    try:
                        self._operate(name,payload)
                    except Exception as exc:
                        # Failed transitions never automatically open another camera.
                        self._fault(str(exc))
                    finally:
                        if self.job is None:
                            with self.mu:
                                self.busy = False
                self._tick()
            except Exception as exc:
                self._fault('监督程序停止当前采集：' + str(exc))
                self.busy = False
        try:
            self._stop_camera(strict=False)
            if self.job is not None:
                self._reap(self.job)
        finally:
            self._unlock()

    def close(self):
        self.quit.set()
        if self.thread:
            self.thread.join(12)


class WebApp:
    def __init__(self, manager, host='192.168.2.99:8080', clients=None):
        self.m = manager
        self.devices = DeviceStatus(manager)
        self.host = host
        self.clients = clients or {'192.168.2.1','192.168.2.99','127.0.0.1'}
        token_file = Path(self.m.data) / '.csrf_token'
        try:
            token = token_file.read_text(encoding='ascii').strip()
            if len(token) != 48 or any(ch not in '0123456789abcdef' for ch in token):
                raise ValueError('invalid token file')
        except Exception:
            token = secrets.token_hex(24)
            try:
                token_file.write_text(token, encoding='ascii')
                os.chmod(token_file, 0o600)
            except OSError:
                logging.exception('csrf token file write failed')
        self.token = token
        self.html = (BASE/'index.html').read_text(encoding='utf-8').replace('__CSRF__',self.token).encode()
        review = BASE/'review.html'
        self.review = (review.read_text(encoding='utf-8').replace('__CSRF__', self.token).encode()
                       if review.is_file() else self.html)

    def __call__(self, env, respond):
        status, mime, body = '200 OK', 'application/json; charset=utf-8', b''
        image_headers = []
        def js(value):
            return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        try:
            if env.get('REMOTE_ADDR') not in self.clients or env.get('HTTP_HOST') != self.host:
                status, body = '403 Forbidden', js({'error':'仅允许直连电脑访问，且 Host 必须匹配板卡地址。'})
            elif env['REQUEST_METHOD'] == 'POST':
                if (env.get('HTTP_X_CSRF_TOKEN') != self.token or
                    env.get('HTTP_ORIGIN', 'http://'+self.host) != 'http://'+self.host or
                    env.get('CONTENT_TYPE','').split(';')[0] != 'application/json'):
                    status, body = '403 Forbidden', js({'error':'请求校验失败，请刷新页面。'})
                else:
                    n = int(env.get('CONTENT_LENGTH') or 0)
                    if not 0 < n <= 2048:
                        raise ValueError('请求长度无效。')
                    payload = json.loads(env['wsgi.input'].read(n))
                    if not isinstance(payload,dict):
                        raise ValueError('请求格式无效。')
                    if env['PATH_INFO']=='/api/lease':
                        body = js(self.m.lease())
                    elif env['PATH_INFO']=='/api/action':
                        body = js(self.m.action(payload.get('action'),payload))
                        status = '202 Accepted'
                    else:
                        status, body = '404 Not Found', js({'error':'Not found'})
            elif env['REQUEST_METHOD'] == 'GET':
                path = env['PATH_INFO']
                if path=='/':
                    body,mime = self.html,'text/html; charset=utf-8'
                elif path in ('/review','/review.html'):
                    body,mime = self.review,'text/html; charset=utf-8'
                elif path=='/api/state':
                    body=js(self.m.snapshot())
                elif path=='/api/devices':
                    body=js(self.devices.snapshot())
                elif path in ('/api/oled/expected.png', '/api/oled/frame.png'):
                    try:
                        body = (self.devices.expected_frame() if path.endswith('expected.png')
                                else self.devices.last_frame())
                        mime = 'image/png'
                    except (ValueError, OSError) as exc:
                        status, body = '503 Service Unavailable', js({'error':str(exc)})
                elif path=='/healthz':
                    if self.m.thread and (not self.m.thread.is_alive() or self.m.clock()-self.m.last_tick > 25):
                        raise RuntimeError('Camera supervisor unavailable')
                    body=js({'ok':True, **self.m.control_output_state()})
                elif path in ('/frame.jpg','/result.jpg'):
                    with self.m.mu:
                        image, at = (self.m.frame,self.m.frame_at) if path=='/frame.jpg' else (self.m.result_image,self.m.result_at)
                        if image is None or (path=='/frame.jpg' and self.m.clock()-at>2):
                            status,body='503 Service Unavailable',js({'error':'暂无新画面'})
                        else:
                            body,mime=image,'image/jpeg'
                            image_headers = [('X-Frame-Sequence', str(self.m.frame_seq if path == '/frame.jpg' else self.m.result_seq)),
                                             ('X-Preview-Rotation', str(self.m.preview_rotation if path == '/frame.jpg' else 0))]
                elif path in ('/photo.png','/report.png','/report.html'):
                    record=self.m.record
                    if not record:
                        raise ValueError('没有当前检查会话。')
                    if path=='/photo.png':
                        file=Path(record['image'])
                        if sha(file)!=record['image_sha256']:
                            raise ValueError('照片校验失败。')
                    else:
                        verify_report(record)
                        file=Path(record['analysis']['report' if path.endswith('html') else 'overlay'])
                    body=file.read_bytes()
                    mime='text/html; charset=utf-8' if path.endswith('html') else 'image/png'
                elif path == '/report.json':
                    record = self.m.record
                    if not record:
                        raise ValueError('没有当前检查会话。')
                    verify_report(record)
                    result_path = Path(record['analysis']['result'])
                    value = read_json(result_path)
                    # Keep the page model-agnostic: future classes are added
                    # to the JSON result, not hard-coded into the UI.
                    body = js(value)
                else:
                    status,body='404 Not Found',js({'error':'Not found'})
            else:
                status,body='405 Method Not Allowed',js({'error':'Method not allowed'})
        except (ValueError, KeyError, OSError) as exc:
            status,body='409 Conflict',js({'error':str(exc)})
        except Exception:
            logging.exception('HTTP request failed')
            status,body='500 Internal Server Error',js({'error':'服务内部错误，请查看运行记录。'})
        headers=image_headers+[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),
                 ('X-Content-Type-Options','nosniff'),('X-Frame-Options','SAMEORIGIN'),
                 ('Referrer-Policy','no-referrer'),('Content-Security-Policy',
                  "default-src 'self'; img-src 'self' blob: data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'")]
        respond(status,headers)
        return [body]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='/home/xilinx/gesture_demo')
    parser.add_argument('--data',default=str(BASE/'data'))
    parser.add_argument('--bind',default='192.168.2.99')
    parser.add_argument('--port',type=int,default=8080)
    args=parser.parse_args()
    Path(args.data).mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(Path(args.data)/'service.log',maxBytes=1024*1024,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO,handlers=[handler])
    sys.path.insert(0,str(BASE/'vendor'))
    from waitress import create_server
    manager=Manager(args.root,args.data)
    server=create_server(WebApp(manager,host='%s:%s'%(args.bind,args.port)),
        host=args.bind,port=args.port,threads=6,connection_limit=32,
        channel_timeout=15,max_request_body_size=4096,max_request_header_size=8192,
        expose_tracebacks=False,clear_untrusted_proxy_headers=True)
    def shutdown(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,shutdown)
    watchdog_stop = threading.Event()
    def notify(message):
        address = os.environ.get('NOTIFY_SOCKET')
        if not address:
            return
        if address.startswith('@'):
            address = '\0' + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)
    def watchdog():
        while not watchdog_stop.wait(5):
            if manager.thread.is_alive() and manager.clock()-manager.last_tick < 25:
                try:
                    notify('WATCHDOG=1')
                except OSError:
                    logging.exception('watchdog notification failed')
    watcher=threading.Thread(target=watchdog,daemon=True,name='service-watchdog')
    try:
        notify('READY=1')
        watcher.start()
        print('PYNQ camera web ready: http://%s:%s/'%(args.bind,args.port),flush=True)
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        watchdog_stop.set()
        server.close()
        manager.close()


if __name__=='__main__':
    mp.freeze_support()
    main()
