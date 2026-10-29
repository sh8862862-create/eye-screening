"""Isolated board benchmark driver; attach through the shared owner only.

No Overlay, clock changes or automatic fallback/reconfiguration. A timed-out
DMA keeps its buffers and lease alive until idle, including at normal exit.
Do not SIGKILL this process while a transaction is outstanding.
"""
import atexit
from contextlib import contextmanager
import sys
import threading
import time
import numpy as np


def sync_words(buffer, words, to_device):
    # PYNQ 3.1's XrtDevice.flush/invalidate ignore slice sizes. Use the native
    # (direction, size_bytes, offset_bytes) overload validated on this board.
    # https://github.com/Xilinx/XRT/blob/master/src/python/pybind11/src/pyxrt.cpp
    import pyxrt
    size = int(words) * 4
    if not 0 < size <= buffer.nbytes:
        raise ValueError('invalid cache synchronization range')
    direction = (pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_TO_DEVICE if to_device
                 else pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_FROM_DEVICE)
    buffer.bo.sync(direction, size, int(buffer.offset))


class PalmPL:
    MAX_WORDS = 294912

    def __init__(self, timeout=3.0):
        from shared_fpga import SharedLease
        from pynq import allocate, MMIO
        self.lease = SharedLease('palm_accel')
        self.buffers = []
        self.closed = False
        self.pending = False
        self.poisoned = False
        self.timeout = timeout
        self.stats = []
        self.cached = None
        self.transaction_thread = None
        self.mmio = MMIO(self.lease.ip['phys_addr'], self.lease.ip['addr_range'])
        if not self.mmio.read(0) & 4:
            self.lease.close()
            raise RuntimeError('palm accelerator is not idle')
        try:
            for count in (self.MAX_WORDS, 6400, 256, self.MAX_WORDS):
                self.buffers.append(allocate(shape=(count,), dtype=np.float32, cacheable=False))
            self.input, self.weights, self.bias, self.output = self.buffers
            for offset, buffer in zip((0x18, 0x24, 0x30, 0x3c), self.buffers):
                self.mmio.write(offset, int(buffer.physical_address) & 0xffffffff)
                self.mmio.write(offset + 4, int(buffer.physical_address) >> 32)
            atexit.register(self._exit_cleanup)
        except BaseException:
            self.close()
            raise

    def forward(self, x, weights, bias, stride, pad, out_h, out_w, destination=None):
        begin = time.perf_counter()
        if self.closed or self.poisoned:
            raise RuntimeError('accelerator is closed or needs recovery')
        if x.ndim != 4 or x.shape[0] != 1:
            raise ValueError('expected a single NCHW input')
        _, channels, height, width = x.shape
        if not (1 <= channels <= 256 and 1 <= height <= 96 and 1 <= width <= 96
                and 1 <= out_h <= 96 and 1 <= out_w <= 96 and stride in (1, 2)
                and pad in (0, 1, 2) and x.size <= self.MAX_WORDS
                and channels*out_h*out_w <= self.MAX_WORDS
                and (out_w-1)*stride+4-pad <= width+1
                and (out_h-1)*stride+4-pad <= height+1
                and weights.shape == (channels, 1, 5, 5) and bias.size == channels):
            raise ValueError('unsupported depthwise shape')
        if self.transaction_thread is None:
            self.lease.check()
        elif self.transaction_thread != threading.get_ident():
            raise RuntimeError('accelerator transaction belongs to another thread')
        checked = time.perf_counter()
        self.input[:x.size] = x.reshape(-1)
        sync_words(self.input, x.size, True)
        # Retain references, so object IDs cannot be recycled under this cache.
        if self.cached is None or self.cached[0] is not weights or self.cached[1] is not bias:
            self.weights[:weights.size] = weights.reshape(-1)
            self.bias[:bias.size] = bias.reshape(-1)
            sync_words(self.weights, weights.size, True)
            sync_words(self.bias, bias.size, True)
            self.cached = (weights, bias)
        for offset, value in zip((0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78),
                                 (channels, height, width, out_h, out_w, stride, pad)):
            self.mmio.write(offset, int(value))
        started = time.perf_counter()
        self.pending = True
        try:
            self.mmio.write(0, 1)
            while not self.mmio.read(0) & 2:
                if time.perf_counter() - started > self.timeout:
                    self.poisoned = True
                    raise TimeoutError('palm DMA timed out; buffers retained until idle')
                time.sleep(.0001)
            self.pending = False
            if self.mmio.read(0x10) != 0:
                raise RuntimeError('palm kernel rejected shape')
            finished = time.perf_counter()
            output_view = self.output[:channels*out_h*out_w]
            sync_words(self.output, channels*out_h*out_w, False)
            if destination is None:
                result = np.array(output_view).reshape(1, channels, out_h, out_w)
            else:
                if destination.shape!=(1,channels,out_h,out_w) or destination.dtype!=np.float32:
                    raise ValueError('invalid destination tensor')
                np.copyto(destination,output_view.reshape(destination.shape))
                result=destination
            self.stats.append(dict(channels=channels, height=height, stride=stride,
                                   lease_ms=(checked-begin)*1000,
                                   upload_ms=(started-checked)*1000,
                                   download_ms=(time.perf_counter()-finished)*1000,
                                   core_ms=(finished-started)*1000,
                                   total_ms=(time.perf_counter()-begin)*1000))
            return result
        except BaseException:
            self.poisoned = True
            raise

    @contextmanager
    def transaction(self):
        """Validate a complete graph once at each boundary.

        The lifetime shared configuration lock already prevents the owner from
        reloading during any layer. Per-layer JSON/sysfs parsing adds latency
        without strengthening that lock. Standalone layer calls still validate.
        """
        if self.closed or self.poisoned or self.transaction_thread is not None:
            raise RuntimeError('accelerator transaction unavailable')
        self.lease.check()
        self.transaction_thread = threading.get_ident()
        try:
            yield self
            self.lease.check()
        finally:
            self.transaction_thread = None

    def close(self):
        if self.closed:
            return
        if self.pending:
            if not self.mmio.read(0) & 4:
                raise RuntimeError('cannot release buffers or lease while DMA is active')
            self.pending = False
        for buffer in self.buffers:
            buffer.freebuffer()
        self.buffers.clear()
        self.lease.close()
        self.closed = True

    def _exit_cleanup(self):
        warned = False
        while self.pending and not self.mmio.read(0) & 4:
            if not warned:
                print('DMA not idle: retaining buffers and shared lease; hardware recovery required',
                      file=sys.stderr, flush=True)
                warned = True
            time.sleep(1)
        self.close()
