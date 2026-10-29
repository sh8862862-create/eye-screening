"""One FPGA owner, with lifetime leases for independent peripheral services.

Only ``python shared_fpga.py load`` downloads a bitstream. Consumers never
construct Overlay, change clocks, or restore base.bit. Locks are advisory for
the installed applications; unrelated root programs can still bypass them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import xml.etree.ElementTree as ET

os.environ.setdefault('XILINX_XRT', '/usr')
DEFAULT_BITFILE = '/home/xilinx/shared_fpga/pynq_shared.bit'
RUNTIME = Path('/run/pynq-shared-fpga')
EXPECTED = {
    'uartlite': ('axi_uartlite', 0x41200000),
    'iic': ('axi_iic', 0x41600000),
    'voice_iic': ('axi_iic', 0x41800000),
    'buttons': ('axi_gpio', 0x40000000),
}
OPTIONAL_ACCELERATORS = {
    'handpose_accel': ('handpose_node12_int16', 0x43C00000),
    'palm_accel': ('palm_depthwise', 0x43C10000),
}


class FPGAError(RuntimeError):
    pass


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    try:
        temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def lock_file(path, *, shared=False):
    import fcntl
    handle = path.open('a+')
    try:
        kind = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(handle.fileno(), kind | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise FPGAError('FPGA/device is in use: %s' % path.name) from exc
    return handle


def hardware_metadata(bitfile):
    """Validate HWH without constructing Overlay or accessing MMIO."""
    root = ET.parse(bitfile.with_suffix('.hwh'))
    ips = {}
    for name, (kind, base) in EXPECTED.items():
        modules = [m for m in root.findall('.//MODULE') if m.get('INSTANCE') == name]
        if len(modules) != 1 or modules[0].get('MODTYPE') != kind:
            raise FPGAError('expected %s IP named %s' % (kind, name))
        params = {p.get('NAME'): p.get('VALUE')
                  for p in modules[0].findall('./PARAMETERS/PARAMETER')}
        if int(params['C_BASEADDR'], 0) != base:
            raise FPGAError('unexpected address for %s' % name)
        if name != 'buttons' and int(params['C_S_AXI_ACLK_FREQ_HZ']) != 62500000:
            raise FPGAError('unexpected clock for %s' % name)
        if name == 'uartlite' and int(params['C_BAUDRATE']) != 9600:
            raise FPGAError('UART must use 9600 baud')
        if name in ('iic', 'voice_iic') and int(params['C_IIC_FREQ']) != 100000:
            raise FPGAError('IIC must use 100 kHz')
        if name == 'buttons' and (int(params['C_GPIO_WIDTH']) != 4 or
                                  int(params['C_ALL_INPUTS']) != 1):
            raise FPGAError('buttons must be four inputs')
        ips[name] = dict(phys_addr=base,
                         addr_range=int(params['C_HIGHADDR'], 0) - base + 1,
                         type=modules[0].get('VLNV'), parameters=params)
    for name, (kind, base) in OPTIONAL_ACCELERATORS.items():
        modules = [m for m in root.findall('.//MODULE') if m.get('INSTANCE') == name]
        if not modules:
            continue  # Original four-peripheral bitstreams remain supported.
        if len(modules) != 1 or modules[0].get('MODTYPE') != kind:
            raise FPGAError('unexpected accelerator type: %s' % name)
        module = modules[0]
        params = {p.get('NAME'): p.get('VALUE')
                  for p in module.findall('./PARAMETERS/PARAMETER')}
        if int(params.get('C_S_AXI_CONTROL_BASEADDR', '-1'), 0) != base or \
                int(params.get('C_S_AXI_CONTROL_HIGHADDR', '-1'), 0) != base + 65535:
            raise FPGAError('unexpected accelerator address: %s' % name)
        clocks = module.findall('./PORTS/PORT[@NAME="ap_clk"]')
        if len(clocks) != 1 or clocks[0].get('CLKFREQUENCY') != '100000000':
            raise FPGAError('accelerator must use 100 MHz: %s' % name)
        ips[name] = dict(phys_addr=base, addr_range=65536,
                         type=module.get('VLNV'), parameters=params)
    return ips


def _pl_identity():
    # Read the disk record freshly: PL.timestamp may be cached in a long-lived
    # Device object. Never construct Overlay(download=False) for identity.
    from pynq.pl_server.global_state import load_global_state, _get_state_path
    value = load_global_state()
    return str(value.bitfile_name), str(value.timestamp), str(_get_state_path())


def load_configuration(bitfile=DEFAULT_BITFILE, runtime=RUNTIME):
    bitfile, runtime = Path(bitfile).resolve(), Path(runtime)
    metadata = hardware_metadata(bitfile)
    bit_hash, hwh_hash = digest(bitfile), digest(bitfile.with_suffix('.hwh'))
    runtime.mkdir(parents=True, exist_ok=True)
    gate = lock_file(runtime / 'configuration.lock')
    try:
        state_file = runtime / 'state.json'
        # Refuse accidental direct double-load; an explicit systemd restart
        # first drains the consumers and revokes the previous generation.
        if state_file.exists():
            old = json.loads(state_file.read_text())
            if old.get('status') == 'ready' and old.get('boot_id') == boot_id():
                raise FPGAError('configuration already ready; restart pynq-fpga.service')
        old_count = 0
        counter = runtime / 'downloads.json'
        if counter.exists():
            old_counter = json.loads(counter.read_text())
            if old_counter.get('boot_id') == boot_id():
                old_count = int(old_counter['count'])
        state = dict(schema_version=1, status='loading', boot_id=boot_id(),
                     generation=str(uuid.uuid4()), bitfile=str(bitfile),
                     bit_sha256=bit_hash, hwh_sha256=hwh_hash, ips=metadata)
        atomic_json(state_file, state)
        # Count attempts before entering the vendor download (also records a
        # failed attempt). No consumer has this code path.
        atomic_json(counter, dict(boot_id=boot_id(), count=old_count + 1))
        try:
            from pynq import Overlay
            overlay = Overlay(str(bitfile))
            for name, descriptor in metadata.items():
                if int(overlay.ip_dict[name]['phys_addr']) != descriptor['phys_addr']:
                    raise FPGAError('PYNQ metadata address mismatch: %s' % name)
            loaded_path, timestamp, identity_file = _pl_identity()
            if Path(loaded_path).resolve() != bitfile:
                raise FPGAError('PYNQ reports another bitfile after download')
            state.update(status='ready', pl_timestamp=timestamp, identity_file=identity_file,
                         configured_at=time.time(), download_count=old_count + 1)
            atomic_json(state_file, state)
        except Exception as exc:
            state.update(status='error', error=str(exc))
            atomic_json(state_file, state)
            raise
        return state
    finally:
        gate.close()


def revoke_configuration(runtime=RUNTIME):
    runtime = Path(runtime)
    gate = lock_file(runtime / 'configuration.lock')
    try:
        state_file = runtime / 'state.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
            state.update(status='stopped')
            atomic_json(state_file, state)
    finally:
        gate.close()


class SharedFPGA:
    """Exclusive peripheral lease plus a shared configuration lifetime lock."""

    def __init__(self, peripheral, runtime=RUNTIME):
        allowed = dict(EXPECTED, **OPTIONAL_ACCELERATORS)
        if peripheral not in allowed:
            raise ValueError('unknown peripheral: %s' % peripheral)
        self.runtime = Path(runtime)
        self.peripheral = peripheral
        self._gate = self._device_lock = None
        self._iic = self._buttons_mmio = None
        self._closed = False
        try:
            # Do not create runtime on attach. Only the owner may prepare it.
            self._gate = lock_file(self.runtime / 'configuration.lock', shared=True)
            # OLED and WonderEcho have separate controllers and pins. Each
            # controller has one lifetime owner, including its MMIO/reset path.
            self._device_lock = lock_file(self.runtime / (peripheral + '.lock'))
            self.state = json.loads((self.runtime / 'state.json').read_text())
            self.generation = self.state['generation']
            self.validate()
            path = Path(self.state['bitfile'])
            if digest(path) != self.state['bit_sha256'] or \
                    digest(path.with_suffix('.hwh')) != self.state['hwh_sha256']:
                raise FPGAError('configured artifacts changed; restart FPGA owner')
            loaded_path, timestamp, _ = _pl_identity()
            if Path(loaded_path).resolve() != path or timestamp != self.state['pl_timestamp']:
                raise FPGAError('FPGA configuration changed outside the owner')
            if peripheral not in self.state['ips']:
                raise FPGAError('configured bitstream does not provide %s' % peripheral)
            self.ip = dict(self.state['ips'][peripheral])
            if self.ip['phys_addr'] != allowed[peripheral][1]:
                raise FPGAError('shared IP address mismatch')
        except Exception:
            self.close()
            raise

    def validate(self):
        if self._closed:
            raise FPGAError('peripheral lease is closed')
        value = json.loads((self.runtime / 'state.json').read_text())
        if value.get('status') != 'ready' or value.get('generation') != self.generation \
                or value.get('boot_id') != boot_id():
            raise FPGAError('shared FPGA generation is unavailable')
        identity = json.loads(Path(value['identity_file']).read_text())
        if identity.get('bitfile_name') != value['bitfile'] or \
                identity.get('timestamp') != value['pl_timestamp']:
            raise FPGAError('FPGA changed outside the shared owner')
        if Path('/sys/class/fpga_manager/fpga0/state').read_text().strip() != 'operating':
            raise FPGAError('FPGA is not operating')
        return value

    check = validate

    @property
    def descriptor(self):
        return self.ip

    @property
    def iic(self):
        if self.peripheral not in ('iic', 'voice_iic'):
            raise FPGAError('IIC access requires an IIC lease')
        self.validate()
        if self._iic is None:
            from pynq.lib.iic import AxiIIC
            self._iic = GuardedIIC(self, AxiIIC(self.ip))
        return self._iic

    def read_buttons(self):
        if self.peripheral != 'buttons':
            raise FPGAError('button access requires a button lease')
        self.validate()
        if self._buttons_mmio is None:
            from pynq import MMIO
            self._buttons_mmio = MMIO(self.ip['phys_addr'], self.ip['addr_range'])
        # Hardware C_ALL_INPUTS=1: no GPIO direction writes needed.
        return self._buttons_mmio.read(0) & 0xF

    def close(self):
        self._closed = True
        self._iic = self._buttons_mmio = None
        for key in ('_device_lock', '_gate'):
            handle = getattr(self, key, None)
            if handle:
                handle.close()
                setattr(self, key, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

SharedLease = SharedFPGA


class GuardedMMIO:
    def __init__(self, lease, mmio):
        self.lease, self.mmio = lease, mmio

    def read(self, *args, **kwargs):
        self.lease.check()
        return self.mmio.read(*args, **kwargs)

    def write(self, *args, **kwargs):
        self.lease.check()
        return self.mmio.write(*args, **kwargs)


class GuardedIIC:
    def __init__(self, lease, driver):
        self.lease, self.driver = lease, driver
        self.mmio = GuardedMMIO(lease, driver.mmio)

    def send(self, *args, **kwargs):
        self.lease.check()
        return self.driver.send(*args, **kwargs)

    def wait(self):
        self.lease.check()
        return self.driver.wait()

    def receive(self, *args, **kwargs):
        self.lease.check()
        return self.driver.receive(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('load', 'stop', 'status'))
    parser.add_argument('--bitfile', default=DEFAULT_BITFILE)
    args = parser.parse_args()
    if args.command == 'load':
        value = load_configuration(args.bitfile)
    elif args.command == 'stop':
        revoke_configuration()
        value = {'status': 'stopped'}
    else:
        value = json.loads((RUNTIME / 'state.json').read_text())
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    main()
