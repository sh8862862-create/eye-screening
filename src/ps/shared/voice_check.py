#!/usr/bin/env python3
"""Inspect WonderEcho without starting a screening session or loading FPGA.

Run under an OS timeout: the vendor native I2C call has no Python deadline.
The default reads one result; --play explicitly selects one prerecorded prompt.
"""
import argparse
import json
import time

from wonderecho import WonderEcho


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--play', type=lambda value: int(value, 0),
                        help='play one ID (for example 0x10); no camera actions')
    parser.add_argument('--listen', type=float, default=0,
                        help='poll results for 0..120 seconds')
    args = parser.parse_args(argv)
    if not 0 <= args.listen <= 120:
        parser.error('--listen must be between 0 and 120 seconds')
    if args.play is not None and not 0 <= args.play <= 255:
        parser.error('--play must fit one byte')
    device = None
    try:
        started = time.monotonic()
        device = WonderEcho()
        print(json.dumps({'status': 'attached', 'address': '0x34',
                          'startup_s': round(time.monotonic() - started, 3),
                          'generation': device.lease.generation}), flush=True)
        initial = device.result()
        print(json.dumps({'initial_result': initial,
                          'elapsed_s': round(time.monotonic() - started, 3)}), flush=True)
        if args.play is not None:
            device.play(args.play)
            print(json.dumps({'played': args.play}), flush=True)
        deadline = time.monotonic() + args.listen
        last = initial
        count = 0
        heartbeat = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = device.result()
            if value or value != last:
                print(json.dumps({'result': value,
                                  'time': time.time()}), flush=True)
            count += 1
            last = value
            if time.monotonic() >= heartbeat:
                print(json.dumps({'status': 'listening', 'polls': count,
                                  'elapsed_s': round(time.monotonic() - started, 3)}), flush=True)
                heartbeat = time.monotonic() + 5
            time.sleep(.05)
        print(json.dumps({'status': 'ok', 'polls': count,
                          'elapsed_s': round(time.monotonic() - started, 3)}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}), flush=True)
        return 1
    finally:
        if device is not None:
            device.close()


if __name__ == '__main__':
    raise SystemExit(main())
