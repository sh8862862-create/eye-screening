"""SSD1306 OLED on the PL I2C bus (Pmod B) of the combined overlay.

The overlay ``pynq_shared.bit`` contains ``axi_uartlite`` (Pmod A, CH9329)
and ``axi_iic`` (Pmod B, OLED).  PYNQ binds the I2C IP to
``pynq.lib.iic.AxiIIC`` automatically, so this module only needs the
overlay's ``iic`` object:

    from oled_iic import SSD1306IIC

    # Use the Overlay object already downloaded and owned by your application.
    # Do not construct a second Overlay(download=False) to prove it is loaded:
    # this PYNQ version updates its software filename even without a download.
    panel = SSD1306IIC(ol.iic)
    panel.show(pil_image_128x64)

Using this instead of ``pynq.lib.Pmod_IIC`` means the OLED never needs
``base.bit``, so it cannot displace the CH9329 bitstream (that conflict is what
wedged the board on 2026-10-21).
"""

from __future__ import annotations

import os
import json
import sys
import time

# pynq needs this in the environment *before* it is imported, otherwise device
# discovery fails with "No Devices Found" (the PYNQ image only exports it for
# login shells).
os.environ.setdefault("XILINX_XRT", "/usr")

W = 128
H = 64
PAGES = H // 8
IISR = 0x20
TX_ERROR = 0x02
ARB_LOST = 0x01
DATA_CHUNK = 32
TRANSFER_GAP = 0.001
TIMING_REGISTERS = tuple(range(0x128, 0x148, 4))
DIAGNOSTIC_REGISTERS = {'IISR': IISR, 'CR': 0x100, 'SR': 0x104,
                        'TX_OCCUPANCY': 0x114, 'RX_OCCUPANCY': 0x118}


def controller_snapshot(iic):
    """Best-effort read-only error evidence; never hide the original error."""
    mmio = getattr(iic, 'mmio', None)
    values = {}
    if mmio is not None:
        for name, offset in DIAGNOSTIC_REGISTERS.items():
            try:
                values[name] = '0x%08X' % mmio.read(offset)
            except Exception:
                values[name] = 'unavailable'
    return values


def reset_iic(iic):
    """Reset this IIC IP only, preserving its timing; never reload the PL."""
    mmio = getattr(iic, 'mmio', None)
    if mmio is None:
        raise RuntimeError('IIC recovery requires controller MMIO')
    # AXI IIC SOFTR resets timing registers as well as FIFO/control state.
    timing = {offset: mmio.read(offset) for offset in TIMING_REGISTERS}
    mmio.write(0x40, 0x0A)
    for offset, value in timing.items():
        mmio.write(offset, value)
    mmio.write(0x100, 0x02)  # Reset TX FIFO while disabled.
    mmio.write(0x100, 0x01)  # Enable, released from master mode.
    time.sleep(TRANSFER_GAP)
    iic.wait()


def validate_address7(address: int) -> int:
    """PYNQ's XIic_Send takes a 7-bit address, never an 8-bit wire byte."""
    if not isinstance(address, int) or not 0x08 <= address <= 0x77:
        raise ValueError('use a 7-bit address (OLED: 0x3C or 0x3D; '
                         '8-bit 0x7A/0x7B means 7-bit 0x3D)')
    return address


def write_checked(iic, address: int, data: bytes) -> None:
    validate_address7(address)
    sent = iic.send(address, data, len(data))
    if sent != len(data):
        raise RuntimeError(f'I2C 0x{address:02X}: short write {sent}/{len(data)}')
    iic.wait()
    # The board's old XIic_Send can return the full length even if the last
    # byte was NACKed. Check the latched hardware errors after STOP as well.
    # XIic_Send clears these flags at the start of the next transaction.
    mmio = getattr(iic, 'mmio', None)
    if mmio is not None:
        status = mmio.read(IISR)
        if status & (TX_ERROR | ARB_LOST):
            reason = 'NACK' if status & TX_ERROR else 'arbitration lost'
            raise RuntimeError(f'I2C 0x{address:02X}: {reason}, IISR=0x{status:08X}')


def probe_oled_checked(iic, address: int) -> None:
    """Write an SSD1306 NOP and preserve the actual failure for diagnostics."""
    validate_address7(address)
    try:
        write_checked(iic, address, b'\x00\xe3')
    except (RuntimeError, OSError) as exc:
        registers = json.dumps(controller_snapshot(iic), sort_keys=True)
        raise RuntimeError('OLED write probe at 0x%02X failed: %s; registers=%s' %
                           (address, exc, registers)) from exc


def probe_oled(iic, address: int) -> bool:
    """Probe an OLED with a command control byte and the harmless E3 NOP.

    SSD1306 I2C is write-only. A read scan cannot establish its presence.
    This is OLED-specific; do not use this command to scan unrelated devices.
    """
    try:
        probe_oled_checked(iic, address)
    except (RuntimeError, OSError):
        return False
    return True


def scan_oled(iic, addresses=(0x3C, 0x3D)) -> list[int]:
    return [address for address in addresses if probe_oled(iic, address)]

# Control bytes: 0x00 = command stream, 0x40 = data stream.
INIT = bytes((
    0x00,       # command stream
    0xAE,       # display off
    0xD5, 0x80,  # clock divide
    0xA8, 0x3F,  # multiplex 1/64
    0xD3, 0x00,  # display offset 0
    0x40,       # start line 0
    0x8D, 0x14,  # charge pump on
    0x20, 0x00,  # horizontal addressing
    0xA1,       # segment remap
    0xC8,       # COM scan dec
    0xDA, 0x12,  # COM pins
    0x81, 0xCF,  # contrast
    0xD9, 0xF1,  # pre-charge
    0xDB, 0x40,  # VCOMH
    0xA4,       # resume to RAM
    0xA6,       # normal (not inverted)
    0xAF,       # display on
))
# AXI IIC's polled C helper on this PYNQ image can return a short write when
# the SSD1306 setup stream is sent as one 26-byte transaction.  Keep each
# command group in its own STOP-delimited transaction.  The OLED accepts a
# repeated command control byte (0x00) between groups, and this also gives us
# a precise failure boundary during startup.
INIT_CHUNKS = (
    b'\x00\xae', b'\x00\xd5\x80', b'\x00\xa8\x3f', b'\x00\xd3\x00',
    b'\x00\x40', b'\x00\x8d\x14', b'\x00\x20\x00', b'\x00\xa1',
    b'\x00\xc8', b'\x00\xda\x12', b'\x00\x81\xcf', b'\x00\xd9\xf1',
    b'\x00\xdb\x40', b'\x00\xa4', b'\x00\xa6', b'\x00\xaf',
)
# Reapply the controller state without intentionally blanking a live panel.
# The final AF is sent after the current framebuffer has been restored.
SYNC_INIT_CHUNKS = INIT_CHUNKS[1:-1]


def pack(img) -> bytes:
    """Convert a PIL image to SSD1306 page format (LSB = top pixel of a page)."""
    img = img.convert("1")
    if img.size != (W, H):
        img = img.resize((W, H))
    px = img.load()
    out = bytearray(W * PAGES)
    for page in range(PAGES):
        base = page * W
        for x in range(W):
            value = 0
            for bit in range(8):
                if px[x, page * 8 + bit]:
                    value |= 1 << bit
            out[base + x] = value
    return bytes(out)


class SSD1306IIC:
    """Minimal SSD1306 driver on top of a PYNQ AxiIIC instance."""

    def __init__(self, iic, address: int = 0x3C) -> None:
        self.iic = iic
        self.address = validate_address7(address)
        self.recovery_count = 0
        self.last_recovery_error = ''
        self._initialize()

    def _initialize(self):
        for index, chunk in enumerate(INIT_CHUNKS):
            self._write(chunk, 'initialize command=%d' % index)

    def _write(self, data, context):
        try:
            write_checked(self.iic, self.address, data)
        except (RuntimeError, OSError) as exc:
            registers = json.dumps(controller_snapshot(self.iic), sort_keys=True)
            raise RuntimeError('%s (%d bytes): %s; registers=%s' %
                               (context, len(data), exc, registers)) from exc
        time.sleep(TRANSFER_GAP)

    @property
    def diagnostics(self):
        return dict(recovery_attempts=self.recovery_count,
                    last_recovery_error=self.last_recovery_error)

    def _window(self) -> None:
        cmd = bytes((0x00, 0x21, 0, W - 1, 0x22, 0, PAGES - 1))
        self._write(cmd, 'window')

    def show(self, img) -> None:
        body = pack(img)
        try:
            self._show_once(body)
        except (RuntimeError, OSError) as first:
            # A single recovery for this panel lifetime, never an endless
            # retry loop. A persistent or second fault fails the service.
            if self.recovery_count:
                raise
            self.recovery_count = 1
            self.last_recovery_error = str(first)
            print('OLED write fault; attempting one controller/panel recovery: %s'
                  % first, file=sys.stderr, flush=True)
            try:
                reset_iic(self.iic)
                self._write(b'\x00\xe3', 'recovery probe')
                self._initialize()
                # A partial frame may have advanced the RAM pointer. Restart
                # at column/page zero and resend every byte before success.
                self._show_once(body)
            except (RuntimeError, OSError) as recovery:
                raise RuntimeError('OLED recovery failed: %s; original: %s' %
                                   (recovery, first)) from recovery
            print('OLED recovery succeeded; full current frame written',
                  file=sys.stderr, flush=True)

    def resync(self, img) -> None:
        """Restore SSD1306 configuration and pixels without a normal blank."""
        body = pack(img)
        try:
            self._sync_once(body)
        except (RuntimeError, OSError) as first:
            if self.recovery_count:
                raise
            self.recovery_count = 1
            self.last_recovery_error = str(first)
            try:
                reset_iic(self.iic)
                self._write(b'\x00\xe3', 'recovery probe')
                self._initialize()
                self._show_once(body)
            except (RuntimeError, OSError) as recovery:
                raise RuntimeError('OLED recovery failed: %s; original: %s' %
                                    (recovery, first)) from recovery

    def _sync_once(self, body):
        for index, chunk in enumerate(SYNC_INIT_CHUNKS):
            self._write(chunk, 'resync command=%d' % index)
        self._show_once(body)
        self._write(b'\x00\xaf', 'resync display on')

    def _show_once(self, body):
        self._window()
        # Every send is a NEW I2C transaction, so EVERY chunk needs 0x40.
        # The horizontal address pointer continues across STOP/START.
        for start in range(0, len(body), DATA_CHUNK):
            self._write(b'\x40' + body[start:start + DATA_CHUNK],
                        'frame offset=%d' % start)

    def clear(self) -> None:
        self.show(_blank())

    def test_pattern(self) -> None:
        self.show(_test_pattern())


def _blank():
    from PIL import Image

    return Image.new("1", (W, H), 0)


def _test_pattern():
    """Border + stripes + a filled block, so a working panel is obvious."""
    from PIL import Image, ImageDraw

    img = Image.new("1", (W, H), 0)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W - 1, H - 1), outline=1)
    for y in range(8, H - 8, 8):
        draw.line((1, y, W - 2, y), fill=1)
    draw.rectangle((8, 8, 56, 32), fill=1)
    draw.text((64, 12), "PYNQ", fill=1)
    draw.text((64, 26), "I2C OK", fill=1)
    return img


# ------------------------------------------------------------------ CLI probe
def main() -> int:
    import argparse
    import sys

    sys.path.insert(0, "/home/xilinx/ch9329_hid")

    parser = argparse.ArgumentParser(description="write-probe OLED addresses and draw a test pattern")
    parser.add_argument("--overlay", default="/home/xilinx/shared_fpga/pynq_shared.bit")
    def address_arg(value):
        try:
            return validate_address7(int(value, 0))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    parser.add_argument("--address", type=address_arg, default=None,
                        help="7-bit OLED address; default tries 0x3C and 0x3D")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--bus-only", action="store_true",
                        help="only check the controller BUS_BUSY flag; does not measure pins")
    parser.add_argument("--no-draw", action="store_true")
    parser.add_argument("--keep-buttons", action="store_true",
                        help="legacy compatibility flag; FPGA is managed by pynq-fpga.service")
    parser.add_argument("--watchdog", type=int, default=0,
                        help="arm the SoC watchdog for N seconds around the I2C work")
    args = parser.parse_args()

    try:
        if args.watchdog:
            from hw_watchdog import HardwareWatchdog

            with HardwareWatchdog(args.watchdog):
                return run(args)
        return run(args)
    except (RuntimeError, OSError) as exc:
        print(f'OLED failed: {exc}', file=sys.stderr)
        return 2


def load_combined_overlay(path=None, keep_buttons=False, owner_unit=None):
    """Attach the unified FPGA owner's IIC lease; never program FPGA here.

    Legacy arguments are accepted only for source compatibility. A named old
    bitfile is rejected so an old CLI command cannot silently use a new image.
    """
    from pathlib import Path
    sys.path.insert(0, '/opt/pynq-shared')
    from shared_fpga import SharedLease, DEFAULT_BITFILE
    if path and Path(path).resolve() != Path(DEFAULT_BITFILE).resolve():
        raise RuntimeError('use shared FPGA configuration: ' + DEFAULT_BITFILE)
    return SharedLease('iic')


def run(args) -> int:
    try:
        overlay = load_combined_overlay(args.overlay, args.keep_buttons)
    except ImportError:
        print("this interpreter has no pynq module.")
        print("use:  sudo /usr/local/share/pynq-venv/bin/python3 %s ..." % __file__)
        print("or:   sudo ch9329-oled --overlay %s" % args.overlay)
        return 2
    try:
        iic = overlay.iic
        print("iic ip:", overlay.descriptor.get("phys_addr", "?"))

        # BUS_BUSY is controller state, not a measurement of the SCL/SDA levels.
        try:
            iic.wait()
            print('controller: not busy (SCL/SDA levels and pull-ups are not measured)')
        except Exception as exc:
            print(f'controller: BUSY / timeout ({exc}); no device transfers attempted')
            return 1
        if args.bus_only:
            return 0

        targets = (args.address,) if args.address is not None else (0x3C, 0x3D)
        print('OLED write probe (7-bit):', ', '.join(f'0x{a:02X}' for a in targets))
        found = scan_oled(iic, targets)
        if found:
            print('OLED ACK:', ', '.join(f'0x{address:02X}' for address in found))
        else:
            print('no OLED write ACK; check supply, wiring and controller/module type')
            return 1
        if args.scan_only:
            return 0 if found else 1

        if len(found) > 1 and not args.no_draw:
            print('multiple OLED addresses ACK; choose one with --address')
            return 1
        if not args.no_draw:
            panel = SSD1306IIC(iic, found[0])
            panel.test_pattern()
            print("test pattern written - the OLED should show a border, stripes and a block")
        return 0
    finally:
        overlay.close()


if __name__ == "__main__":
    raise SystemExit(main())
