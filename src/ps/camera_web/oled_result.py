#!/usr/bin/env python3
"""Eye-screening result display for a 128x64 SSD1306 OLED (I2C).

The OLED is the device-side result panel: it shows the model conclusion while
the PC browser shows the full-resolution photo and handles gesture review.
It reads the same files the web service writes, so it works without a browser.

    # render a preview PNG (no hardware)
    python3 oled_result.py --preview preview.png --mock result --report demo/report.json
    python3 oled_result.py --preview preview_neg.png --mock negative

    # PYNQ-Z2: combined UART + IIC overlay, OLED on Pmod B1/B2
    sudo /usr/local/share/pynq-venv/bin/python3 oled_result.py --pl-iic --address 0x3c
"""
import argparse
import hashlib
import io
import json
import math
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

try:
    import numpy as np
except ImportError:  # pragma: no cover - the PYNQ venv always has numpy
    np = None

W, H = 128, 64
DEFAULT_DATA = Path('/home/xilinx/gesture_demo/camera_web/data')
FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/simhei.ttf',
)
LATIN_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'C:/Windows/Fonts/arial.ttf',
    'C:/Windows/Fonts/segoeui.ttf',
)

MOCK_GESTURE = {
    'ok': True, 'inference_active': True, 'result_stale': False,
    'result_age_s': 1.1, 'result_seq': 42, 'camera_active': True,
    'result': {'mode': 'SCROLL', 'event': 'CONTENT_DOWN', 'pipeline_ms': 2810.0},
}


def pick_font(explicit=None):
    for path in ([explicit] if explicit else []) + list(FONT_CANDIDATES):
        if path and Path(path).is_file():
            return path
    return None


def pick_latin(explicit=None):
    for path in ([explicit] if explicit else []) + list(LATIN_CANDIDATES):
        if path and Path(path).is_file():
            return path
    return None


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class DisplayStatus:
    """Publish daemon health and only those frames the display accepted.

    The PNG is committed before its digest is published. Readers must compare
    that digest before using the image, since the two files cannot be replaced
    in one filesystem transaction.
    """

    def __init__(self, data_dir, clock=time.time):
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        try:
            boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            boot_id = None
        self.value = dict(schema_version=1, status='initializing', pid=os.getpid(),
                          boot_id=boot_id, updated_at=None, last_write_at=None,
                          last_frame_sha256=None, last_state=None, last_page=None,
                          last_check_id=None, error='', recovery_attempts=0,
                          last_recovery_error='')
        self.update()

    def _atomic(self, name, content):
        path = self.data / name
        temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
        try:
            temporary.write_bytes(content)
            os.chmod(temporary, 0o644)
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def update(self, **changes):
        self.value.update(changes)
        self.value['updated_at'] = self.clock()
        content = json.dumps(self.value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self._atomic('oled_status.json', content)

    def frame_written(self, image, state, page, record, flip=False, invert=False):
        # Mirror the transforms performed by the display backends so the PNG
        # represents the bytes accepted by the screen with these CLI options.
        if flip:
            image = image.rotate(180)
        if invert:
            image = image.point(lambda value: 0 if value else 1, '1')
        encoded = io.BytesIO()
        image.save(encoded, format='PNG')
        content = encoded.getvalue()
        self._atomic('oled_last_frame.png', content)
        self.update(status='ready', last_write_at=self.clock(),
                    last_frame_sha256=hashlib.sha256(content).hexdigest(),
                    last_state=state, last_page=page + 1,
                    last_check_id=(record or {}).get('check_id'), error='')

    def failed(self, error):
        self.update(status='error', error=str(error))

    def capture_hardware(self, display):
        # Include recovery evidence with the next frame/error publication.
        # Reading these software counters does not access I2C or configure PL.
        info = getattr(display, 'diagnostics', None)
        if isinstance(info, dict):
            self.value.update({key: info[key] for key in
                               ('recovery_attempts', 'last_recovery_error', 'generation') if key in info})

    def stopped(self):
        # A failing process must retain its error after resources are closed.
        if self.value['status'] != 'error':
            self.update(status='stopped')


def normalize_scores(report):
    if not isinstance(report, dict):
        return []
    c = report.get('classification') or {}
    if not isinstance(c, dict):
        return []
    raw = c.get('scores')
    if isinstance(raw, dict):
        raw = [dict({'label': k}, **v) if isinstance(v, dict)
               else {'label': k, 'score': v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or 'score' not in item:
            continue
        try:
            score = float(item['score'])
            threshold = float(item.get('threshold', 0.5))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or not math.isfinite(threshold):
            continue
        positive = item.get('positive') is True or (item.get('positive') is not False and score >= threshold)
        out.append({
            'label': item.get('label_zh') or item.get('label') or '目标',
            'score': score, 'threshold': threshold, 'positive': positive,
        })
    out.sort(key=lambda x: (not x['positive'], -x['score']))
    return out


_QUALITY_CACHE = {}


def image_quality(path):
    """Board-side rough photo check, matching the web page's thresholds.

    lap<40 -> 清晰度低, mean<55/>205 -> 曝光异常, std<18 -> 对比偏低.
    Any hit means the OLED asks for a retake.
    """
    if not path or np is None:
        return None
    try:
        stat = os.stat(path)
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = _QUALITY_CACHE.get(key)
        if cached is not None:
            return cached
        image = Image.open(path).convert('L')
        if image.width > 480:
            height = max(1, round(image.height * 480 / image.width))
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            image = image.resize((480, height), resample)
        pixels = np.asarray(image, dtype=np.float32)
        mean = float(pixels.mean())
        std = float(pixels.std())
        lap = (-4 * pixels[1:-1, 1:-1] + pixels[1:-1, :-2] + pixels[1:-1, 2:]
               + pixels[:-2, 1:-1] + pixels[2:, 1:-1])
        sharpness = float((lap * lap).mean())
        reasons = []
        if sharpness < 40:
            reasons.append('清晰度低')
        if mean < 55:
            reasons.append('画面偏暗')
        elif mean > 205:
            reasons.append('画面偏亮')
        if std < 18:
            reasons.append('对比偏低')
        result = {'lap': sharpness, 'mean': mean, 'std': std,
                  'reasons': reasons, 'retake': bool(reasons)}
        _QUALITY_CACHE[key] = result
        return result
    except Exception:
        return None


def read_state(data_dir):
    """Return ('idle'|'confirm'|'analyzing'|'result'|'error', record, report)."""
    current = Path(data_dir) / 'current.json'
    try:
        name = load_json(current).get('session')
    except Exception:
        return 'idle', None, None
    if not name:
        return 'idle', None, None
    session = Path(data_dir) / name
    try:
        record = load_json(session / 'session.json')
    except Exception:
        return 'idle', None, None
    if not isinstance(record, dict):
        return 'idle', None, None
    record['_session'] = str(name)
    state = record.get('state')
    if state in ('RESULT_READY', 'GESTURE_TEST_FINISHED'):
        report = None
        try:
            report = load_json(record['analysis']['result'])
            if not isinstance(report, dict):
                raise ValueError('report must be a JSON object')
        except Exception:
            report = None
            record['_report_error'] = '报告读取失败'
        record['_quality'] = image_quality(record.get('image'))
        return 'result', record, report
    if state == 'AWAITING_IMAGE_REVIEW':
        record['_quality'] = image_quality(record.get('image'))
        return 'confirm', record, None
    if state in ('EYE_PROCESSING_CAMERAS_CLOSED', 'EYE_CAPTURE'):
        return 'analyzing', record, None
    if state == 'ERROR':
        return 'error', record, None
    return 'idle', record, None


def _font(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _width(draw, text, cjk, latin):
    total = 0
    for ch in text:
        total += draw.textlength(ch, font=(latin if ord(ch) < 128 else cjk))
    return total


def _truncate(draw, text, cjk, latin, max_width):
    if _width(draw, text, cjk, latin) <= max_width:
        return text
    out = ''
    for ch in text:
        if _width(draw, out + ch + '…', cjk, latin) > max_width:
            break
        out += ch
    return out + '…' if out else ''


def _text(draw, xy, text, cjk, latin, fill=1):
    x, y = xy
    for ch in text:
        ft = latin if ord(ch) < 128 else cjk
        draw.text((x, y), ch, font=ft, fill=fill)
        x += draw.textlength(ch, font=ft)
    return x


def split_url(url):
    """'192.168.2.99:8080/review' -> ('192.168.2.99', ':8080/review')."""
    raw = url if '://' in url else '//' + url
    try:
        parts = urlsplit(raw)
        host = parts.hostname or url
        port = parts.port
        path = parts.path or ''
    except ValueError:
        return url, ''
    return host, ((':%d' % port) if port else '') + path


def read_gesture(api='192.168.2.99:8080', timeout=1.5):
    """Read gesture state from the local web service (no CSRF needed for GET)."""
    host = api.replace('http://', '').replace('https://', '').strip('/')
    base = api if api.startswith('http') else 'http://' + api
    try:
        request = urllib.request.Request(base.rstrip('/') + '/api/state',
                                         headers={'Host': host})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception:
        return {'ok': False}
    return {
        'ok': True,
        'inference_active': bool(data.get('inference_active')),
        'result_stale': bool(data.get('result_stale')),
        'result_age_s': data.get('result_age_s'),
        'result_seq': data.get('result_seq'),
        'result': data.get('result') or {},
        'role': data.get('role'),
        'camera_active': bool(data.get('camera_active')),
        'report_ready': bool(data.get('report_ready')),
        'session_id': data.get('session_id'),
    }


def _new_page(font_path, latin_path):
    img = Image.new('1', (W, H), 0)
    draw = ImageDraw.Draw(img)

    def f(size):
        return _font(font_path, size), _font(latin_path, size)

    return img, draw, f


def _render_report_page(font_path, latin_path, report_url, record):
    img, draw, f = _new_page(font_path, latin_path)
    f_title, f_big, f_body, f_small = f(10), f(14), f(11), f(9)
    _text(draw, (2, 1), '检查报告', f_title[0], f_title[1])
    right = '待检查' if (record or {}).get('_report_error') else '完成'
    x = W - 2 - _width(draw, right, f_small[0], f_small[1])
    _text(draw, (max(0, x), 2), right, f_small[0], f_small[1])
    draw.line((0, 14, W - 1, 14), fill=1)

    host, rest = split_url(report_url)
    host = _truncate(draw, host, f_big[0], f_big[1], W - 4)
    _text(draw, (2, 16), host, f_big[0], f_big[1])
    if rest:
        rest = _truncate(draw, rest, f_body[0], f_body[1], W - 4)
        _text(draw, (2, 35), rest, f_body[0], f_body[1])

    check = (record or {}).get('check_id') or ''
    foot = ('检查号 ' + check[:8]) if check else '电脑端打开阅片页'
    foot = _truncate(draw, foot, f_small[0], f_small[1], W - 4)
    _text(draw, (2, 49), foot, f_small[0], f_small[1])
    return img


def _render_gesture_page(font_path, latin_path, gesture):
    img, draw, f = _new_page(font_path, latin_path)
    f_title, f_big, f_body, f_small = f(10), f(14), f(11), f(9)
    g = gesture or {}
    ok = bool(g.get('ok'))
    result = (g.get('result') or {}) if ok else {}
    mode = (result.get('mode') or 'IDLE').upper()
    event = (result.get('event') or '').upper()
    active = bool(g.get('inference_active'))
    stale = bool(g.get('result_stale'))
    camera = bool(g.get('camera_active'))

    if not ok:
        right_text = '离线'
    elif not active:
        right_text = '未开启'
    elif stale:
        right_text = '等待'
    else:
        right_text = {'SCROLL': '滚动', 'ZOOM': '缩放'}.get(mode, '待机')

    _text(draw, (2, 1), '手势控制', f_title[0], f_title[1])
    x = W - 2 - _width(draw, right_text, f_small[0], f_small[1])
    _text(draw, (max(0, x), 2), right_text, f_small[0], f_small[1])
    draw.line((0, 14, W - 1, 14), fill=1)

    if not ok:
        big, body, foot = '手势状态未知', 'Web 服务未响应', ''
    elif not active:
        big, body = '手势未开启', '电脑端启动阅片'
        foot = 'A 相机未开启' if not camera else '识别未运行'
    elif stale:
        big, body, foot = '等待识别', '识别约 3 秒 / 次', ''
    elif event == 'NO_HAND_RELEASE':
        big, body, foot = '未检测到手', '把手放进 A 相机', ''
    elif event in ('PINCH_HOLD', 'PINCH_RELEASE_HOLD'):
        big, body, foot = '捏合确认中', '再保持 1-2 秒', ''
    elif mode == 'SCROLL':
        big = '滚动已激活'
        body = ('内容向下' if event == 'CONTENT_DOWN' else
                '内容向上' if event == 'CONTENT_UP' else '只伸食指移动')
        foot = ''
    elif mode == 'ZOOM':
        big = '缩放已激活'
        body = ('放大' if event == 'ZOOM_IN' else
                '缩小' if event == 'ZOOM_OUT' else '拇指 / 食指距离')
        foot = ''
    elif event in ('PINCH_RELEASED', 'DEACTIVATE'):
        big, body, foot = '已退出控制', '捏合可再次激活', ''
    elif event == 'ACTIVATE':
        big, body, foot = '已激活', '伸食指滚动 / 张掌缩放', ''
    else:
        big, body, foot = '等待手势', '捏合激活 / 张掌缩放', ''

    if active:
        pipeline = result.get('pipeline_ms')
        age = g.get('result_age_s')
        if pipeline:
            foot = '识别%.1fs / %.1fs前' % (float(pipeline) / 1000,
                                          float(age) if age is not None else 0.0)
        elif not foot:
            foot = '等待识别结果'

    _text(draw, (2, 16), big, f_big[0], f_big[1])
    _text(draw, (2, 35), _truncate(draw, body, f_body[0], f_body[1], W - 4), f_body[0], f_body[1])
    if foot:
        _text(draw, (2, 49), foot, f_small[0], f_small[1])
    return img


def render(state, record=None, report=None, font_path=None, latin_path=None,
           page=0, gesture=None, report_url='192.168.2.99:8080/review'):
    """Render one 128x64 1-bit frame (CJK and Latin usually need two fonts)."""
    img = Image.new('1', (W, H), 0)
    draw = ImageDraw.Draw(img)
    font_path = font_path or pick_font()
    latin_path = latin_path or pick_latin()

    if state == 'result' and page == 1:
        return _render_report_page(font_path, latin_path, report_url, record)
    if state == 'result' and page == 2:
        return _render_gesture_page(font_path, latin_path, gesture)

    def f(size):
        return _font(font_path, size), _font(latin_path, size)

    f_title, f_big, f_body, f_small = f(10), f(14), f(11), f(9)

    def right(text, y, pair):
        x = W - 2 - _width(draw, text, pair[0], pair[1])
        _text(draw, (max(0, x), y), text, pair[0], pair[1])

    _text(draw, (2, 1), '眼表筛查', f_title[0], f_title[1])

    if state == 'idle':
        right('待机', 2, f_small)
        draw.line((0, 14, W - 1, 14), fill=1)
        _text(draw, (2, 16), '等待拍照', f_big[0], f_big[1])
        _text(draw, (2, 35), '请先采集眼部影像', f_body[0], f_body[1])
        _text(draw, (2, 49), '电脑端可预览与手势阅片', f_small[0], f_small[1])
        return img

    if state == 'confirm':
        quality = (record or {}).get('_quality') or {}
        if quality.get('retake'):
            right('重拍', 2, f_small)
            draw.line((0, 14, W - 1, 14), fill=1)
            _text(draw, (2, 16), '请重拍', f_big[0], f_big[1])
            reason = ' / '.join(quality.get('reasons') or ['画面质量偏低'])
            reason = _truncate(draw, reason, f_body[0], f_body[1], W - 4)
            _text(draw, (2, 35), reason, f_body[0], f_body[1])
            _text(draw, (2, 49), '电脑端点重拍', f_small[0], f_small[1])
            return img
        right('待确认', 2, f_small)
        draw.line((0, 14, W - 1, 14), fill=1)
        _text(draw, (2, 16), '照片已采集', f_big[0], f_big[1])
        body = '质量正常，请确认影像' if quality else '请在电脑端确认影像'
        _text(draw, (2, 35), body, f_body[0], f_body[1])
        _text(draw, (2, 49), '确认后开始分析', f_small[0], f_small[1])
        return img

    if state == 'analyzing':
        right('分析中', 2, f_small)
        draw.line((0, 14, W - 1, 14), fill=1)
        _text(draw, (2, 16), '正在分析…', f_big[0], f_big[1])
        _text(draw, (2, 35), '相机已关闭', f_body[0], f_body[1])
        _text(draw, (2, 49), '请稍候，自动显示结果', f_small[0], f_small[1])
        return img

    if state == 'error':
        right('异常', 2, f_small)
        draw.line((0, 14, W - 1, 14), fill=1)
        _text(draw, (2, 16), '分析失败', f_big[0], f_big[1])
        msg = (record or {}).get('error') or '请查看电脑端提示'
        msg = _truncate(draw, msg, f_body[0], f_body[1], W - 4)
        _text(draw, (2, 35), msg, f_body[0], f_body[1])
        _text(draw, (2, 49), '可在电脑端重试', f_small[0], f_small[1])
        return img

    if (record or {}).get('_report_error'):
        right('异常', 2, f_small)
        draw.line((0, 14, W - 1, 14), fill=1)
        _text(draw, (2, 16), '报告不可用', f_big[0], f_big[1])
        _text(draw, (2, 35), '报告读取失败', f_body[0], f_body[1])
        _text(draw, (2, 49), '请在电脑端检查或重试', f_small[0], f_small[1])
        return img

    scores = normalize_scores(report)
    positives = [x for x in scores if x['positive'] and x['label'] != '正常']
    normal = next((x for x in scores if x['label'] == '正常' and x['positive']), None)
    right('完成', 2, f_small)
    draw.line((0, 14, W - 1, 14), fill=1)

    if normal and not positives and (report or {}).get('classification', {}).get('decision') == 'normal':
        big = '正常'
        line = '正常分数 %.1f%%' % (normal['score'] * 100)
        sub = '模型判定 / 不替代检查'
    elif positives:
        big = '检出 %d 项' % len(positives)
        top = positives[0]
        line = '%s %.1f%%' % (top['label'], top['score'] * 100)
        if len(positives) > 1:
            sub = '另有%d项 / 阈值%.0f%%' % (len(positives) - 1, top['threshold'] * 100)
        else:
            sub = '阈值%.0f%% / 高于%.1fpt' % (top['threshold'] * 100, (top['score'] - top['threshold']) * 100)
    elif scores:
        big = '未确定'
        line = '未满足分类判定条件'
        sub = '请复核影像或重拍'
    else:
        big = '无分类结果'
        line = '报告未包含分数'
        sub = '电脑端查看详情'

    quality = (record or {}).get('_quality') or {}
    if quality.get('retake'):
        sub = '%s / 建议重拍' % ((quality.get('reasons') or ['质量低'])[0])

    # Keep report access on the live result, without a timed page carousel.
    result_body, result_small, address_font = f(10), f(8), f(7)
    _text(draw, (2, 15), big, f_big[0], f_big[1])
    _text(draw, (2, 32), _truncate(draw, line, *result_body, W - 4), *result_body)
    _text(draw, (2, 45), _truncate(draw, sub, *result_small, W - 4), *result_small)
    host, rest = split_url(report_url)
    address = _truncate(draw, host + rest, *address_font, W - 4)
    _text(draw, (2, 54), address, *address_font)
    return img


def pack(img):
    if img.mode != '1':
        img = img.convert('1')
    px = img.load()
    buf = bytearray(1024)
    for page in range(8):
        base = page * W
        for x in range(W):
            value = 0
            for bit in range(8):
                if px[x, page * 8 + bit]:
                    value |= 1 << bit
            buf[base + x] = value
    return buf


class SSD1306:
    I2C_SLAVE = 0x0703
    INIT = (0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14,
            0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1,
            0xDB, 0x40, 0xA4, 0xA6, 0xAF)

    def __init__(self, bus=1, address=0x3C, flip=False, invert=False):
        import fcntl
        self.fd = os.open('/dev/i2c-%d' % bus, os.O_RDWR)
        fcntl.ioctl(self.fd, self.I2C_SLAVE, address)
        self.flip = flip
        self.invert = invert
        self._cmd(*self.INIT)

    def _write(self, control, data):
        data = bytes(data)
        for start in range(0, len(data), 32):
            os.write(self.fd, bytes([control]) + data[start:start + 32])

    def _cmd(self, *commands):
        self._write(0x00, commands)

    def show(self, img):
        if self.flip:
            img = img.rotate(180)
        if self.invert:
            img = img.point(lambda v: 0 if v else 1, '1')
        self._cmd(0x21, 0, W - 1, 0x22, 0, 7)
        self._write(0x40, pack(img))

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


class PLDisplay:
    """Business frames on the verified shared-configuration AXI IIC driver."""

    def __init__(self, address=0x3C, flip=False, invert=False,
                 overlay='/home/xilinx/shared_fpga/pynq_shared.bit'):
        sys.path.insert(0, '/home/xilinx/ch9329_hid')
        from oled_iic import (load_combined_overlay, probe_oled_checked,
                              reset_iic, SSD1306IIC, validate_address7)

        validate_address7(address)
        self.flip = flip
        self.invert = invert
        self.overlay = load_combined_overlay(overlay)
        try:
            iic = self.overlay.iic
            # Reset only this AXI IIC instance, preserving its timing. Probe
            # failures must retain their real error (NACK, busy, stale lease,
            # short transfer, etc.) instead of collapsing into "no ACK".
            reset_iic(iic)
            probe_oled_checked(iic, address)
            self.panel = SSD1306IIC(iic, address)
        except Exception:
            self.overlay.close()
            raise

    def show(self, img):
        if self.flip:
            img = img.rotate(180)
        if self.invert:
            img = img.point(lambda v: 0 if v else 1, '1')
        self.panel.show(img)

    def resync(self, img):
        if self.flip:
            img = img.rotate(180)
        if self.invert:
            img = img.point(lambda v: 0 if v else 1, '1')
        self.panel.resync(img)

    @property
    def diagnostics(self):
        return dict(self.panel.diagnostics, generation=getattr(self.overlay, 'generation', None))

    def close(self):
        # Leave the last page visible, then release only this IIC lease.
        self.overlay.close()


class PmodDisplay:
    """SSD1306 on a PYNQ Pmod port via the PL-side Pmod_IIC (not /dev/i2c).

    base.PMODA / base.PMODB are MicroBlaze mailbox infos; Pmod_IIC takes pin
    indices 0..3 (Pmod pins 1..4), so the standard wiring is SCL=0, SDA=1.
    Loading the base overlay replaces the PL bitstream.
    """

    def __init__(self, port='B', address=0x3C, flip=False, invert=False,
                 scl_pin=0, sda_pin=1,
                 bitstream='/home/xilinx/pynq/overlays/base/base.bit'):
        raise RuntimeError('legacy base.bit display is disabled; use --pl-iic '
                           'with pynq-fpga.service')

    def show(self, img):
        if self.flip:
            img = img.rotate(180)
        if self.invert:
            img = img.point(lambda v: 0 if v else 1, '1')
        data = pack(img)
        self.iic.send([0x00, 0x21, 0, W - 1, 0x22, 0, 7])
        for start in range(0, len(data), 32):
            self.iic.send([0x40] + list(data[start:start + 32]))

    def close(self):
        pass


MOCK_REPORT = {
    'classification': {
        'scores': [
            {'label_zh': '睑裂斑', 'score': 0.76, 'threshold': 0.8, 'positive': False},
            {'label_zh': '结膜下出血', 'score': 0.487, 'threshold': 0.55, 'positive': False},
            {'label_zh': '翼状胬肉', 'score': 0.038, 'threshold': 0.55, 'positive': False},
        ]
    }
}


def mock_state(name):
    if name == 'result':
        report = load_json(Path(__file__).with_name('demo') / 'report.json')
        return 'result', None, report
    if name == 'negative':
        return 'result', None, MOCK_REPORT
    if name == 'retake':
        return 'confirm', {'_quality': {'retake': True, 'reasons': ['清晰度低']}}, None
    if name == 'blur':
        report = load_json(Path(__file__).with_name('demo') / 'report.json')
        return 'result', {'_quality': {'retake': True, 'reasons': ['清晰度低']}}, report
    if name == 'error':
        return 'error', {'error': '模型进程退出，请重试'}, None
    return name, None, None


class PageCycle:
    """Keep the live conclusion visible; fresh actions may show gesture status."""

    def __init__(self, initial_page=0, fixed_page=None):
        self.page = initial_page
        self.fixed_page = fixed_page
        self.key = None
        self.page_at = None
        self.hold_until = 0.0
        self.last_seq = None

    def update(self, state, record, gesture, now):
        record = record or {}
        key = (state, record.get('_session'), record.get('check_id'))
        if self.page_at is None:
            self.page_at = now
        elif key != self.key:
            self.page = 0
            self.page_at = now
            self.hold_until = 0.0
            self.last_seq = None
        self.key = key
        if self.fixed_page is not None:
            return self.fixed_page
        g = gesture or {}
        expected_session = record.get('check_id') or record.get('session_id')
        same_session = (not expected_session or g.get('session_id') == expected_session)
        valid = (g.get('ok') and g.get('inference_active')
                 and not g.get('result_stale') and same_session)
        if state == 'result' and valid:
            result = g.get('result') or {}
            seq = g.get('result_seq')
            event = (result.get('event') or '').upper()
            mode = (result.get('mode') or '').upper()
            if seq is not None and seq != self.last_seq:
                self.last_seq = seq
                if event not in ('', 'WAIT', 'NO_HAND_RELEASE'):
                    self.page = 2
                    self.page_at = now
                    self.hold_until = now + 5
            if mode in ('SCROLL', 'ZOOM'):
                self.page = 2
                self.page_at = now
                self.hold_until = max(self.hold_until, now + 2)
        if self.page == 2 and now >= self.hold_until:
            self.page = 0
            self.page_at = now
        return self.page


def main():
    parser = argparse.ArgumentParser(description='128x64 eye-screening result display')
    parser.add_argument('--data', default=str(DEFAULT_DATA))
    parser.add_argument('--preview', help='write a scaled PNG instead of driving I2C')
    parser.add_argument('--scale', type=int, default=5)
    parser.add_argument('--mock', choices=('result', 'negative', 'retake', 'blur', 'report', 'gesture',
                                           'idle', 'confirm', 'analyzing', 'error'))
    parser.add_argument('--page', type=int, choices=(1, 2, 3), help='result page: 1 结论, 2 报告地址, 3 手势')
    parser.add_argument('--report', help='result.json to render with --mock result')
    parser.add_argument('--api', default='192.168.2.99:8080', help='web service for gesture state')
    parser.add_argument('--report-url', default='192.168.2.99:8080/review')
    parser.add_argument('--font')
    parser.add_argument('--latin-font')
    parser.add_argument('--bus', type=int, default=1)
    parser.add_argument('--address', type=lambda v: int(v, 0), default=0x3C)
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument('--pl-iic', action='store_true',
                         help='shared FPGA IIC: OLED on Pmod B1/B2')
    parser.add_argument('--overlay', default='/home/xilinx/shared_fpga/pynq_shared.bit')
    backend.add_argument('--pmod', choices=('A', 'B'),
                        help='use PL-side Pmod IIC instead of /dev/i2c')
    parser.add_argument('--pmod-scl', type=int, default=0)
    parser.add_argument('--pmod-sda', type=int, default=1)
    parser.add_argument('--pmod-bitstream',
                        default='/home/xilinx/pynq/overlays/base/base.bit')
    parser.add_argument('--flip', action='store_true')
    parser.add_argument('--invert', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    font_path = pick_font(args.font)
    latin_path = pick_latin(args.latin_font)
    page = 0
    gesture = None
    if args.mock in ('report', 'gesture'):
        state = 'result'
        record = {'check_id': 'e8f069af-0d59-49f7-84e4-351a2f279789'}
        report = load_json(Path(__file__).with_name('demo') / 'report.json')
        page = 1 if args.mock == 'report' else 2
        gesture = MOCK_GESTURE if args.mock == 'gesture' else None
        if args.report:
            report = load_json(args.report)
    elif args.mock:
        state, record, report = mock_state(args.mock)
        if args.report and state == 'result':
            report = load_json(args.report)
    else:
        state, record, report = read_state(args.data)
    if args.page:
        page = args.page - 1
    if state == 'result' and page == 2 and gesture is None:
        gesture = read_gesture(args.api)

    img = render(state, record, report, font_path, latin_path, page, gesture, args.report_url)

    if args.preview:
        preview = img.convert('RGB').resize((W * args.scale, H * args.scale), Image.NEAREST)
        preview.save(args.preview)
        print('preview:', args.preview, preview.size)
        return 0

    status = DisplayStatus(args.data)
    if not font_path:
        status.failed('a Chinese font is required')
        print('display not available: a Chinese font is required', file=sys.stderr)
        return 2

    display = None
    last = None
    last_hardware_sync = 0.0
    HARDWARE_SYNC_SECONDS = 15.0
    cycle = PageCycle(page, args.page - 1 if args.page else None)
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        while not stop_requested:
            if not args.mock:
                state, record, report = read_state(args.data)
            gesture_state = read_gesture(args.api) if state == 'result' else None
            page = cycle.update(state, record, gesture_state, time.monotonic())
            img = render(state, record, report, font_path, latin_path, page, gesture_state, args.report_url)
            digest = img.tobytes()
            if display is None:
                try:
                    if args.pl_iic:
                        display = PLDisplay(args.address, args.flip, args.invert, args.overlay)
                        print('display: shared FPGA IIC / Pmod B @ 0x%02x' % args.address, flush=True)
                    elif args.pmod:
                        display = PmodDisplay(args.pmod, args.address, args.flip, args.invert,
                                              args.pmod_scl, args.pmod_sda, args.pmod_bitstream)
                        print('display: Pmod %s @ 0x%02x' % (args.pmod, args.address), flush=True)
                    else:
                        display = SSD1306(args.bus, args.address, args.flip, args.invert)
                        print('display: /dev/i2c-%d @ 0x%02x' % (args.bus, args.address), flush=True)
                    # Start any temporary gesture display after hardware is ready.
                    cycle.page_at = time.monotonic()
                except Exception as exc:
                    status.failed(exc)
                    print('display not available: %s' % exc, file=sys.stderr, flush=True)
                    return 2
            if stop_requested:
                break
            now = time.monotonic()
            needs_sync = last is not None and now - last_hardware_sync >= HARDWARE_SYNC_SECONDS
            if digest != last or needs_sync:
                try:
                    if digest != last:
                        display.show(img)
                    else:
                        getattr(display, 'resync', display.show)(img)
                except Exception as exc:
                    status.capture_hardware(display)
                    status.failed(exc)
                    print('display write failed: %s' % exc, file=sys.stderr, flush=True)
                    return 2
                # A render is only evidence of success after show has returned.
                status.capture_hardware(display)
                status.frame_written(img, state, page, record, args.flip, args.invert)
                last = digest
                last_hardware_sync = now
                print('frame: state=%s page=%d check=%s' % (
                    state, page + 1, (record or {}).get('check_id', '')[:8]), flush=True)
            else:
                status.update()
            if args.once:
                return 0
            time.sleep(1)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        status.failed(exc)
        raise
    finally:
        try:
            if display is not None:
                display.close()
        finally:
            status.stopped()
            signal.signal(signal.SIGTERM, previous_sigterm)



if __name__ == '__main__':
    sys.exit(main())
