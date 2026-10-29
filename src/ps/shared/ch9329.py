"""CH9329 / CH9329F serial protocol (protocol transmission mode, 0x00).

Frame layout (all values hex, datasheet V1.3 section 2.1):

    57 AB | ADDR | CMD | LEN | DATA (0..64) | SUM
    SUM = (57 + AB + ADDR + CMD + LEN + all DATA bytes) & 0xFF

The chip answers a good command with ``CMD | 0x80`` and one status byte
(0x00 = success), and an error with ``CMD | 0xC0`` plus an error status
(0xE1..0xE6).  This module only implements the normal (one byte length)
frames that the mouse/keyboard commands use; the CH9329F extension commands
with a two byte length are deliberately out of scope.

The class works on top of a byte transport (see ``uart_lite.py`` or
``serial_port.py``), so it can be unit tested with a simulated device.
"""

from __future__ import annotations

import time

HEAD = b"\x57\xAB"

CMD_GET_INFO = 0x01
CMD_SEND_KB_GENERAL_DATA = 0x02
CMD_SEND_KB_MEDIA_DATA = 0x03
CMD_SEND_MS_ABS_DATA = 0x04
CMD_SEND_MS_REL_DATA = 0x05
CMD_GET_PARA_CFG = 0x08
CMD_RESET = 0x0F

STATUS_CODES = {
    0x00: "success",
    0xE1: "serial receive timeout",
    0xE2: "bad frame header",
    0xE3: "bad command code",
    0xE4: "checksum mismatch",
    0xE5: "bad parameter",
    0xE6: "frame ok but command failed",
}

# USB HID keyboard modifier bitmap (first data byte of CMD_SEND_KB_GENERAL_DATA).
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

# Mouse button bitmap: bit0 left, bit1 right, bit2 middle.
BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04


class Ch9329Error(RuntimeError):
    """Raised for protocol violations or a device reported failure."""


class Ch9329Timeout(Ch9329Error):
    """No complete answer frame arrived before the deadline."""


def checksum(body: bytes) -> int:
    """SUM covers every byte of the frame except SUM itself."""
    return sum(body) & 0xFF


def build_frame(address: int, command: int, data: bytes = b"") -> bytes:
    """Return one normal CH9329 protocol frame."""
    if not 0 <= address <= 0xFF:
        raise ValueError("address must be 0..255")
    if not 0 <= command <= 0xFF:
        raise ValueError("command must be 0..255")
    data = bytes(data)
    if len(data) > 64:
        raise ValueError("CH9329 normal frames carry at most 64 data bytes")
    body = HEAD + bytes((address, command, len(data))) + data
    return body + bytes((checksum(body),))


def encode_signed(value: int, name: str, limit: int = 127) -> int:
    """Encode a signed byte the way the CH9329 expects (two's complement)."""
    if not -limit <= value <= limit:
        raise ValueError(f"{name} must be in -{limit}..{limit}")
    return value & 0xFF


class Frame:
    __slots__ = ("address", "command", "data")

    def __init__(self, address: int, command: int, data: bytes) -> None:
        self.address = address
        self.command = command
        self.data = data

    def __repr__(self) -> str:
        return (
            f"Frame(address=0x{self.address:02X}, command=0x{self.command:02X},"
            f" data={self.data.hex(' ')})"
        )


def parse_frames(buffer: bytes):
    """Split ``buffer`` into complete frames.

    Returns ``(frames, remainder)``.  Bytes before the 0x57 0xAB header are
    skipped, which is what the datasheet recommends when the UART has noise
    or a previous answer was cut short.
    """
    frames = []
    index = 0
    length = len(buffer)
    while True:
        start = buffer.find(HEAD, index)
        if start < 0:
            # Keep a trailing 0x57: it may be the first half of a header.
            remainder = b"\x57" if buffer.endswith(b"\x57") else b""
            return frames, remainder
        if length - start < 6:
            return frames, buffer[start:]
        address = buffer[start + 2]
        command = buffer[start + 3]
        data_len = buffer[start + 4]
        end = start + 5 + data_len
        if length < end + 1:
            return frames, buffer[start:]
        data = buffer[start + 5 : end]
        received = buffer[end]
        body = buffer[start:end]
        if checksum(body) != received:
            # Bad SUM: drop one byte and resync, never trust the payload.
            index = start + 1
            continue
        frames.append(Frame(address, command, data))
        index = end + 1


class Transport:
    """Minimal byte transport interface used by :class:`Ch9329`."""

    def write(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, max_bytes: int, timeout: float) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def reset_input(self) -> None:  # pragma: no cover - optional
        pass

    def close(self) -> None:  # pragma: no cover - optional
        pass


class Ch9329:
    """Synchronous CH9329 client with retries and strict answer checking."""

    def __init__(
        self,
        transport: Transport,
        address: int = 0x00,
        timeout: float = 0.35,
        retries: int = 2,
        clock=time.monotonic,
    ) -> None:
        self.transport = transport
        self.address = address
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.clock = clock
        self._buffer = b""
        self.stats = {"sent": 0, "retried": 0, "failed": 0, "timeouts": 0}

    # ------------------------------------------------------------- plumbing
    def _read_frame(self, deadline: float) -> Frame:
        while True:
            frames, rest = parse_frames(self._buffer)
            self._buffer = rest
            if frames:
                return frames[0]
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise Ch9329Timeout(
                    f"no complete answer frame within {self.timeout:.2f}s "
                    f"(buffered {self._buffer.hex(' ')})"
                )
            chunk = self.transport.read(64, min(remaining, 0.05))
            if chunk:
                self._buffer += chunk

    def transact(self, command: int, data: bytes = b"", expect: int | None = None,
                 status_reply: bool = True) -> bytes:
        """Send one command and return the payload of its answer frame.

        ``status_reply`` is False for commands whose answer payload is data
        rather than a single execution status (only CMD_GET_INFO here).
        """
        packet = build_frame(self.address, command, data)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                self.transport.reset_input()
                self._buffer = b""
                self.transport.write(packet)
                self.stats["sent"] += 1
                frame = self._read_frame(self.clock() + self.timeout)
            except Ch9329Timeout as exc:
                self.stats["timeouts"] += 1
                last_error = exc
                self.stats["retried"] += 1
                continue
            if frame.address != self.address:
                raise Ch9329Error(
                    f"answer address 0x{frame.address:02X} != 0x{self.address:02X}"
                )
            if frame.command == command:
                # Receiving our own command back means TX is shorted to RX:
                # the loopback jumper on Pmod A pins 1/2 is still installed, or
                # the module's TX/RX are bridged.  The chip never echoes 0x01.
                raise Ch9329Error(
                    f"received our own command 0x{command:02X} back - TX is "
                    "shorted to RX (remove the 1-2 loopback jumper before "
                    "connecting the CH9329)"
                )
            if frame.command == (command | 0xC0):
                status = frame.data[0] if frame.data else 0xFF
                raise Ch9329Error(
                    f"command 0x{command:02X} rejected: 0x{status:02X} "
                    f"({STATUS_CODES.get(status, 'unknown')})"
                )
            if expect is not None and frame.command != expect:
                raise Ch9329Error(
                    f"answer command 0x{frame.command:02X} != 0x{expect:02X}"
                )
            if status_reply and frame.command == (command | 0x80) and frame.data:
                status = frame.data[0]
                if status != 0x00:
                    raise Ch9329Error(
                        f"command 0x{command:02X} failed: 0x{status:02X} "
                        f"({STATUS_CODES.get(status, 'unknown')})"
                    )
            if attempt:
                self.stats["retried"] += 1
            return frame.data
        self.stats["failed"] += 1
        raise last_error if last_error else Ch9329Timeout("command failed")

    # -------------------------------------------------------------- commands
    def get_info(self) -> dict:
        """Return version / USB state / keyboard LED / sleep state."""
        payload = self.transact(CMD_GET_INFO, b"", expect=0x81, status_reply=False)
        if len(payload) != 8:
            raise Ch9329Error(f"CMD_GET_INFO answered {len(payload)} bytes, expected 8")
        return {
            "version": f"V{payload[0] >> 4}.{payload[0] & 0x0F}",
            "version_raw": payload[0],
            "usb_connected": payload[1] == 0x01,
            "connected_raw": payload[1],
            "leds": {
                "num_lock": bool(payload[2] & 0x01),
                "caps_lock": bool(payload[2] & 0x02),
                "scroll_lock": bool(payload[2] & 0x04),
            },
            "sleep_raw": payload[3],
            "sleeping": payload[3] == 0x03,
            "raw": payload.hex(" "),
        }

    def keyboard(self, modifier: int = 0, keys=()) -> None:
        """Press ``modifier`` bits together with up to six HID key codes."""
        keys = list(keys)[:6]
        if modifier & ~0xFF:
            raise ValueError("modifier must be a byte")
        data = bytes([modifier & 0xFF, 0x00] + list(keys) + [0] * (6 - len(keys)))
        self.transact(CMD_SEND_KB_GENERAL_DATA, data, expect=0x82)

    def mouse_rel(self, dx: int = 0, dy: int = 0, buttons: int = 0, wheel: int = 0) -> None:
        """Relative mouse report; dx/dy in -127..127 pixels, wheel in notches."""
        if buttons & ~0x07:
            raise ValueError("buttons only use bits 0..2")
        data = bytes(
            (
                0x01,
                buttons & 0x07,
                encode_signed(dx, "dx"),
                encode_signed(dy, "dy"),
                encode_signed(wheel, "wheel"),
            )
        )
        self.transact(CMD_SEND_MS_REL_DATA, data, expect=0x85)

    def mouse_abs(self, x: int, y: int, buttons: int = 0, wheel: int = 0) -> None:
        """Absolute mouse report in the chip's 0..4095 coordinate space."""
        for value, name in ((x, "x"), (y, "y")):
            if not 0 <= value <= 4095:
                raise ValueError(f"absolute {name} must be 0..4095")
        if buttons & ~0x07:
            raise ValueError("buttons only use bits 0..2")
        data = bytes(
            (
                0x02,
                buttons & 0x07,
                x & 0xFF,
                (x >> 8) & 0xFF,
                y & 0xFF,
                (y >> 8) & 0xFF,
                encode_signed(wheel, "wheel"),
            )
        )
        self.transact(CMD_SEND_MS_ABS_DATA, data, expect=0x84)

    def wheel(self, notches: int) -> None:
        """Scroll ``notches`` clicks (positive = up, negative = down)."""
        if notches == 0:
            return
        step = 1 if notches > 0 else -1
        for _ in range(min(abs(int(notches)), 127)):
            self.mouse_rel(wheel=step)

    def release_all(self) -> bool:
        """Release every key/button. Best effort: never raises."""
        ok = True
        try:
            self.keyboard(0, ())
        except Exception:
            ok = False
        try:
            self.mouse_rel(0, 0, 0, 0)
        except Exception:
            ok = False
        return ok

    def reset(self) -> None:
        """Software reset of the chip (answers before rebooting)."""
        self.transact(CMD_RESET, b"", expect=0x8F)

    def close(self) -> None:
        try:
            self.release_all()
        finally:
            self.transport.close()
