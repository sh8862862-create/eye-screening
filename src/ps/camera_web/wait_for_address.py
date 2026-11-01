"""Allow a booting or reconnected board to acquire its existing static address."""
import socket
import sys
import time

end = time.monotonic() + 90
while time.monotonic() < end:
    try:
        with socket.socket() as probe:
            probe.bind(('192.168.2.99', 0))
        sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit('Existing board address is not ready; systemd will retry.')
