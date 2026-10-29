"""AXI UARTLite byte transport for the PYNQ-Z2 PL serial port.

The overlay built by ``vivado/build_uart_overlay.tcl`` puts one Xilinx
``axi_uartlite`` in the PS ``M_AXI_GP0`` window at 0x4120_0000:

    Pmod A pin 1 (Y18) = TX  -> CH9329 RXD
    Pmod A pin 2 (Y19) = RX  <- CH9329 TXD
    Pmod A pin 5/11    = GND

Register map (PG142):

    0x00 RX FIFO  (read)       0x04 TX FIFO (write)
    0x08 STAT     0x0C CTRL

STAT bits, verified against the generated ``axi_uartlite`` VHDL and by reading
0x00000004 from an idle, freshly configured core (TX empty, RX empty):

    bit0 RX FIFO valid data   bit1 RX FIFO full
    bit2 TX FIFO empty        bit3 TX FIFO full
    bit4 interrupt enable     bit5..7 overrun/frame/parity error

Access needs root (``/dev/mem`` is ``root:kmem 0640`` on the PYNQ image), which
is why the bridge service runs as root, exactly like ``pynq-buttons.service``.
"""

from __future__ import annotations

import ctypes
import os
import time

REG_RX = 0x00
REG_TX = 0x04
REG_STAT = 0x08
REG_CTRL = 0x0C

STAT_RX_VALID = 1 << 0
STAT_RX_FULL = 1 << 1
STAT_TX_EMPTY = 1 << 2
STAT_TX_FULL = 1 << 3

CTRL_RESET_TX = 1 << 0
CTRL_RESET_RX = 1 << 1


class UartLiteError(RuntimeError):
    pass


class UartLiteTransport:
    """Byte transport on top of the UARTLite registers."""

    def __init__(
        self,
        base: int = 0x41200000,
        baud: int = 9600,
        clock_hz: int = 62_500_000,
        path: str = "/dev/mem",
        clock=time.monotonic,
        shared_lease=None,
    ) -> None:
        # All production UART entry points, including standalone diagnostics,
        # participate in the same lease. Tests using a fake memory path need
        # not have PYNQ installed.
        self._lease = shared_lease
        self._owns_lease = False
        if path == '/dev/mem':
            if self._lease is None:
                from shared_fpga import SharedLease
                self._lease = SharedLease('uartlite')
                self._owns_lease = True
            self._lease.check()
            if base != self._lease.descriptor['phys_addr'] or baud != 9600 or clock_hz != 62500000:
                if self._owns_lease:
                    self._lease.close()
                raise UartLiteError('UART settings do not match shared FPGA')
        self.base = int(base)
        self.baud = int(baud)
        self.clock_hz = int(clock_hz)
        self.clock = clock
        self.path = path
        # 8N1 = 10 bit times per byte.  Pacing writes to this rate is not an
        # optimisation, it is required: see write() and README section 12.
        self.byte_time = 10.0 / max(self.baud, 1)
        self._page = self.base & ~0xFFF
        self._offset = self.base - self._page
        self._fd = None
        self._map = None
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_SYNC)
            self._map = self._mmap()
            # Registers must be touched through an aligned 32-bit ctypes view.
            # Measured 2026-10-21: struct.pack_into/unpack_from on the mmap
            # works for the first few accesses and then draws an AXI error
            # (SIGBUS) from the TX FIFO register, while identical stores through
            # this ctypes view never fail.  Do not "simplify" this back.
            self._word = (ctypes.c_uint32 * 4).from_buffer(self._map, self._offset)
        except BaseException:
            if self._map is not None:
                self._map.close()
            if self._fd is not None:
                os.close(self._fd)
            if self._owns_lease:
                self._lease.close()
            raise
        self.stats = {"written": 0, "read": 0, "tx_full_waits": 0, "rx_timeouts": 0}

    # ----------------------------------------------------------- low level
    def _mmap(self):
        import mmap as _mmap

        return _mmap.mmap(
            self._fd,
            0x1000,
            _mmap.MAP_SHARED,
            _mmap.PROT_READ | _mmap.PROT_WRITE,
            offset=self._page,
        )

    def _reg(self, offset: int) -> int:
        if self._lease is not None:
            self._lease.check()
        return int(self._word[offset >> 2])

    def _set_reg(self, offset: int, value: int) -> None:
        if self._lease is not None:
            self._lease.check()
        self._word[offset >> 2] = value & 0xFFFF_FFFF

    def _write_byte(self, value: int) -> None:
        self._set_reg(REG_TX, value & 0xFF)

    def _read_byte(self) -> int:
        return self._reg(REG_RX) & 0xFF

    # -------------------------------------------------------------- public
    def reset(self) -> None:
        """Reset both FIFOs; also clears a half-received frame."""
        self._set_reg(REG_CTRL, CTRL_RESET_TX | CTRL_RESET_RX)
        time.sleep(0.0002)
        self._set_reg(REG_CTRL, 0)

    def reset_input(self) -> None:
        """Drop whatever is still sitting in the RX FIFO."""
        for _ in range(64):
            if not (self._reg(REG_STAT) & STAT_RX_VALID):
                return
            self._read_byte()

    def write(self, data: bytes) -> None:
        """Write bytes at (at most) the wire rate.

        AXI UARTLite returns ``ip2bus_error`` - an AXI SLVERR, i.e. SIGBUS in
        user space - when the TX FIFO is written while full.  Measured on the
        board (2026-10-21): four back-to-back stores are enough to trigger it,
        while the same stores paced at one byte per wire time never do.  So
        although the FIFO is 16 deep, each byte here waits for the previous one
        to leave the FIFO before the next is written.
        """
        deadline = self.clock() + max(1.0, len(data) * self.byte_time * 2.0) + 1.0
        for index, byte in enumerate(data):
            if index:
                time.sleep(self.byte_time)
            while self._reg(REG_STAT) & STAT_TX_FULL:
                self.stats["tx_full_waits"] += 1
                if self.clock() > deadline:
                    raise UartLiteError("UARTLite TX FIFO stayed full")
                time.sleep(0.0002)
            self._write_byte(byte)
            self.stats["written"] += 1

    def read(self, max_bytes: int, timeout: float) -> bytes:
        """Read up to ``max_bytes`` bytes, waiting at most ``timeout`` seconds."""
        deadline = self.clock() + timeout
        out = bytearray()
        while len(out) < max_bytes:
            if self._reg(REG_STAT) & STAT_RX_VALID:
                out.append(self._read_byte())
                continue
            if self.clock() >= deadline:
                break
            # A UART byte takes baud/10 s; sleeping much less wastes CPU.
            time.sleep(0.0002)
        if not out:
            self.stats["rx_timeouts"] += 1
        self.stats["read"] += len(out)
        return bytes(out)

    def close(self) -> None:
        try:
            # The ctypes view exports a buffer from the mmap; drop it first or
            # mmap.close() raises BufferError("cannot close exported pointers").
            self._word = None
            if self._map is not None:
                self._map.close()
                self._map = None
        finally:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            if self._owns_lease:
                self._lease.close()
