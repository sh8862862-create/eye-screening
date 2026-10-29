"""Legacy configuration entry points are disabled by the shared FPGA owner."""
class OverlayError(RuntimeError):
    pass

def _managed(*args, **kwargs):
    raise OverlayError('FPGA is managed by pynq-fpga.service; use systemctl restart '
                       'pynq-fpga.service for controlled reconfiguration. '
                       'Consumers must use shared_fpga.SharedLease.')

load_bitstream = _managed
load_with_pynq = _managed
load_with_fpga_manager = _managed
set_pl_clock = _managed
restore_base = _managed

if __name__ == '__main__':
    _managed()
