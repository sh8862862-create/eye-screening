"""Opt-in serial protocol for an external USB-HID mouse bridge.

The PYNQ-Z2 USART is not itself a USB HID device.  This module therefore
only emits a small, newline-delimited protocol to a configured serial device;
an explicitly installed computer-side bridge must turn those messages into
mouse/HID events.  With no PYNQ_HID_SERIAL environment variable, nothing is
opened and no control output is produced.
"""
import json
import os
import termios
import time


class SerialMouseBridge:
    def __init__(self, device, baud=115200):
        if not device:
            raise ValueError('serial HID device is empty')
        self.device = device
        self.fd = os.open(device, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure(int(baud))
        except BaseException:
            os.close(self.fd)
            raise
        self.last_sent = 0.0

    def _configure(self, baud):
        table = {9600: termios.B9600, 19200: termios.B19200,
                 38400: termios.B38400, 57600: termios.B57600,
                 115200: termios.B115200}
        if baud not in table:
            raise ValueError('unsupported serial baud rate')
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = attrs[5] = table[baud]
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def send(self, result, captured_at=None):
        event = result.get('event')
        if event not in ('CONTENT_UP', 'CONTENT_DOWN', 'ZOOM_IN', 'ZOOM_OUT'):
            return False
        now = time.monotonic()
        if captured_at is not None and now - captured_at > 1.0:
            return False
        if now - self.last_sent < .08:
            return False
        message = {
            'protocol': 'pynq-usart-mouse-v1',
            'event': event,
            'wheel': int(round(float(result.get('scroll_px', 0.0)))),
            'zoom_factor': float(result.get('zoom_factor', 1.0)),
            'ts_monotonic': round(now, 6),
        }
        data = (json.dumps(message, separators=(',', ':')) + '\n').encode('ascii')
        try:
            os.write(self.fd, data)
        except BlockingIOError:
            # A stalled bridge must not take down camera acquisition; the
            # current motion sample is intentionally dropped.
            return False
        self.last_sent = now
        return True

    def close(self):
        if getattr(self, 'fd', None) is not None:
            os.close(self.fd)
            self.fd = None
