"""Read-only device diagnostics; never configures the FPGA or accesses I2C."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import threading
import time


def _boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return None


def camera_status(configured, expected, sys_root=Path('/sys/class/video4linux')):
    path = Path(configured or '')
    device, usb = None, None
    if configured:
        try:
            resolved = path.resolve(strict=True)
            # A configured filename alone is insufficient evidence of a USB match.
            if re.fullmatch(r'video\d+', resolved.name):
                device = str(resolved)
                ancestry = (sys_root / resolved.name / 'device').resolve(strict=True)
                for parent in (ancestry,) + tuple(ancestry.parents):
                    match = re.fullmatch(r'\d+-(\d+(?:\.\d+)*)(?::\d+\.\d+)?', parent.name)
                    if match:
                        usb = match.group(1)
                        break
        except (OSError, RuntimeError):
            pass
    return dict(expected_usb=expected, configured_path=configured, device=device,
                present=device is not None, usb_path=usb,
                matched=device is not None and usb == expected)


class DeviceStatus:
    def __init__(self, manager, clock=time.time, service_reader=None, boot_reader=_boot_id):
        self.manager, self.clock = manager, clock
        self.service_reader = service_reader or self._service
        self.boot_reader = boot_reader
        self._cached_at = 0
        self._service_value = 'unknown'
        self._lock = threading.Lock()

    @staticmethod
    def _service():
        try:
            result = subprocess.run(['systemctl', 'is-active', 'eye-oled.service'],
                                    capture_output=True, text=True, timeout=1.5)
            value = result.stdout.strip()
            return value if value in ('active', 'activating', 'inactive', 'failed', 'deactivating') else 'unknown'
        except (OSError, subprocess.TimeoutExpired):
            return 'unknown'

    def _status(self):
        try:
            value = json.loads((self.manager.data / 'oled_status.json').read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def snapshot(self):
        now = self.clock()
        with self._lock:
            if now - self._cached_at > 2 or self._cached_at == 0:
                self._service_value = self.service_reader()
                self._cached_at = now
            service = self._service_value
        s = self._status()
        boot = self.boot_reader()
        same_boot = bool(boot and s.get('boot_id') == boot)
        heartbeat = s.get('updated_at')
        fresh = same_boot and isinstance(heartbeat, (int, float)) and 0 <= now-heartbeat < 8
        current = (self.manager.record or {}).get('check_id')
        last_check = s.get('last_check_id') if same_boot else None
        digest = s.get('last_frame_sha256') if same_boot else None
        written = s.get('last_write_at') if same_boot else None
        state = s.get('status', 'unknown') if same_boot else 'unknown'
        if service in ('inactive', 'deactivating'):
            state = 'stopped'
        elif service == 'failed':
            state = 'error'
        elif not fresh:
            state = 'unknown'
        web = self.manager.snapshot()
        return dict(cameras={role: camera_status(self.manager.config.get(role), expected)
                             for role, expected in (('eye', '1.4'), ('gesture', '1.3'))},
                    oled=dict(service=service, state=state, heartbeat_fresh=fresh,
                              check_id=last_check, current_check_id=current,
                              session_match=bool(written and last_check == current and same_boot),
                              last_write_at=written, frame_sha256=digest,
                              last_write_age_s=round(max(0, now-written), 1) if isinstance(written, (int, float)) else None,
                              error=s.get('error', '') if same_boot else '',
                              frame_url=('/api/oled/frame.png?v='+digest) if digest and written else None),
                    # The default OLED page renders workflow state, not hand
                    # inference progress. Keep its image stable between results.
                    expected_key='|'.join(str(web.get(k) or '') for k in
                                          ('session_id', 'workflow_state', 'phase', 'error')),
                    mock=False)

    def last_frame(self):
        s = self._status()
        if not self.boot_reader() or s.get('boot_id') != self.boot_reader():
            raise ValueError('本次启动尚无 OLED 成功写入记录。')
        content = (self.manager.data / 'oled_last_frame.png').read_bytes()
        if not s.get('last_write_at') or hashlib.sha256(content).hexdigest() != s.get('last_frame_sha256'):
            raise ValueError('OLED 写屏画面正在更新，请稍后刷新。')
        return content

    def expected_frame(self):
        # Importing the renderer is hardware-free. PLDisplay is constructed only by its CLI.
        import oled_result
        state, record, report = oled_result.read_state(self.manager.data)
        font = oled_result.pick_font()
        if not font:
            raise ValueError('未找到 OLED 中文字体。')
        image = oled_result.render(state, record, report, font, oled_result.pick_latin())
        encoded = io.BytesIO()
        image.save(encoded, format='PNG')
        return encoded.getvalue()
