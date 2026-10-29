"""Read the gesture output target without changing FPGA or worker state."""
import json
from pathlib import Path


MODES = ('web', 'pl_uart', 'serial_hid', 'disabled')


def read_output_mode(path, serial_device=None):
    """Missing config preserves the legacy target; invalid config stops routing."""
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return ('serial_hid' if serial_device else 'web'), ''
    except (OSError, ValueError) as exc:
        return 'disabled', '手势输出配置无法读取：' + str(exc)
    mode = config.get('mode') if isinstance(config, dict) else None
    if mode not in MODES:
        return 'disabled', '手势输出配置的 mode 无效。'
    if mode == 'serial_hid' and not serial_device:
        return 'disabled', '内部串口 HID 尚未配置串口设备。'
    return mode, ''
