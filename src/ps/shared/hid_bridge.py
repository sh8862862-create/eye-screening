#!/usr/bin/env python3
"""CH9329 HID bridge for the PYNQ-Z2 gesture pipeline.

Reads the gesture result that ``pynq-camera-web.service`` already publishes on
``/api/state``, turns it into CH9329 USB-HID reports and sends them over the
PL UART (AXI UARTLite on Pmod A) to the CH9329 module, whose USB side is
plugged into the PC.

    PYNQ-Z2 (this script, root)          CH9329                 PC
    gesture event -> frame 57 AB ... --> UART --> USB HID --> scroll / zoom

Why a separate process instead of a hook inside ``camera_web``?
    ``/dev/mem`` is ``root:kmem 0640`` on this image, so only root can touch the
    UARTLite. ``pynq-camera-web.service`` runs as ``xilinx``; keeping the HID
    bridge separate keeps the validated camera service untouched and lets the
    bridge be stopped or restarted without risking a running session.

Usage on the board:

    sudo -E /usr/local/share/pynq-venv/bin/python3 hid_bridge.py

    # query UART/CH9329 without any mouse or keyboard reports:
    sudo ... hid_bridge.py --query-only

    # bench test without the web service / camera:
    sudo ... hid_bridge.py --replay gestures.jsonl --no-require-report
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ch9329 import Ch9329, Ch9329Error, Ch9329Timeout  # noqa: E402
from gesture_hid import GestureHid  # noqa: E402

DEFAULT_API = "http://192.168.2.99:8080/api/state"
DEFAULT_LOG_DIR = "/home/xilinx/ch9329_hid/logs"


class Recorder:
    """Stand-in for :class:`Ch9329` used by ``--dry-run``."""

    def __init__(self) -> None:
        self.frames = []

    def keyboard(self, modifier: int = 0, keys=()) -> None:
        self.frames.append({"type": "keyboard", "modifier": modifier, "keys": list(keys)})

    def mouse_rel(self, dx: int = 0, dy: int = 0, buttons: int = 0, wheel: int = 0) -> None:
        self.frames.append(
            {"type": "mouse_rel", "dx": dx, "dy": dy, "buttons": buttons, "wheel": wheel}
        )

    def close(self) -> None:
        pass


class JsonlLog:
    def __init__(self, path: Path | None) -> None:
        self.fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(path, "a", encoding="utf-8", buffering=1)

    def __call__(self, record: dict) -> None:
        if self.fh is None:
            return
        try:
            self.fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def close(self) -> None:
        if self.fh is not None:
            self.fh.close()
            self.fh = None


def fetch_state(url: str, timeout: float) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("camera state is not an object")
    return value


class SharedUartTransport:
    """Keep a configuration/device lease for every UART access."""

    def __init__(self, baud, clock_hz, base):
        from shared_fpga import SharedLease
        from uart_lite import UartLiteTransport

        self.lease = SharedLease("uartlite")
        self.raw = None
        try:
            self.lease.check()
            address = self.lease.descriptor["phys_addr"]
            if base != address or clock_hz != 62_500_000 or baud != 9600:
                raise RuntimeError("shared UART requires its configured address, 62.5 MHz and 9600 baud")
            self.raw = UartLiteTransport(base=address, baud=baud, clock_hz=clock_hz,
                                         shared_lease=self.lease)
            self.reset()
        except BaseException:
            self.close()
            raise

    @property
    def stats(self):
        return self.raw.stats

    @property
    def generation(self):
        return self.lease.generation

    def check(self):
        self.lease.check()

    def reset(self):
        self.check()
        self.raw.reset()

    def reset_input(self):
        self.check()
        self.raw.reset_input()

    def read(self, max_bytes, timeout):
        self.check()
        return self.raw.read(max_bytes, timeout)

    def write(self, data):
        self.check()
        return self.raw.write(data)

    def close(self):
        try:
            raw, self.raw = self.raw, None
            if raw is not None:
                raw.close()
        finally:
            self.lease.close()


def open_link(args):
    """Build the transport + CH9329 client (or the dry-run recorder)."""
    if args.dry_run:
        return Recorder(), "dry-run"

    if args.transport == "serial":
        from serial_port import PosixSerial

        transport = PosixSerial(args.device, args.baud)
        return Ch9329(transport, timeout=args.timeout, retries=args.retries), args.device

    transport = SharedUartTransport(args.baud, args.clock_hz, args.base)
    return Ch9329(transport, timeout=args.timeout, retries=args.retries), f"uartlite@0x{args.base:08X}"


def link_check(link, args, log) -> dict:
    if args.dry_run:
        return {"ok": True, "dry_run": True}
    last = None
    for attempt in range(3):
        try:
            info = link.get_info()
            log({"kind": "link", "ok": True, "attempt": attempt + 1, **info})
            print(
                f"CH9329 {info['version']} USB={'connected' if info['usb_connected'] else 'NOT connected'} "
                f"({info['raw']})"
            )
            return {"ok": True, **info}
        except (Ch9329Error, Ch9329Timeout) as exc:
            last = exc
            time.sleep(0.2)
    log({"kind": "link", "ok": False, "error": str(last)})
    print(f"CH9329 did not answer: {last}", file=sys.stderr)
    return {"ok": False, "error": str(last)}


def run(args) -> int:
    if args.overlay or args.set_clock or args.overlay_method != "auto":
        raise RuntimeError("FPGA configuration and clocks belong to pynq-fpga.service; remove --overlay/--set-clock/--overlay-method")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = None if args.no_log else Path(args.log_dir) / f"hid_{stamp}.jsonl"
    log = JsonlLog(log_path)

    link = None
    try:
        link, device = open_link(args)
        return run_link(args, link, device, log)
    finally:
        log.close()
        if link is not None:
            if args.query_only and hasattr(link, "transport"):
                # Ch9329.close() emits keyboard/mouse release reports. A
                # read-only query closes the UART directly to send only 0x01.
                link.transport.close()
            else:
                link.close()


def run_link(args, link, device, log) -> int:
    generation = getattr(getattr(link, "transport", None), "generation", None)

    head = {
        "kind": "start",
        "device": device,
        "api": args.api,
        "dry_run": args.dry_run,
        "generation": generation,
        "pid": os.getpid(),
        "t": round(time.time(), 3),
    }
    log(head)
    print(f"bridge start: device={device} api={args.api} dry_run={args.dry_run}")

    check = link_check(link, args, log)
    status_path = Path(args.status_file) if args.status_file else None
    if status_path:
        write_status(status_path, dict(t=time.time(), pid=os.getpid(),
                     status="ready" if check.get("ok") else "error", device=device,
                     generation=generation, link=check, query_only=args.query_only))
    if not check.get("ok") and not args.dry_run:
        print(
            "No answer from the CH9329. Check TX/RX crossing, Pmod A pins 1/2, "
            "3.3 V TTL level, the chip's CFG0/CFG1 protocol mode, and that the "
            "shared FPGA service is ready.",
            file=sys.stderr,
        )
        return 2
    if args.query_only:
        # Intentionally avoid GestureHid and its shutdown release frames.
        print(json.dumps(dict(device=device, generation=generation, link=check)))
        return 0

    mapper = GestureHid(
        link, scroll_gain=args.scroll_gain, zoom_step=args.zoom_step,
        max_step=args.max_step, min_scroll_px=args.min_scroll_px,
        invert_wheel=args.invert_wheel, ctrl_timeout=args.ctrl_timeout, log=log,
    )

    stop = {"flag": False}

    def _stop(signum, frame):
        stop["flag"] = True

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    last_seq = -1
    started = time.monotonic()
    deadline = started + args.duration if args.duration else None
    errors = 0
    last_status = 0.0
    last_event = None
    final_status = "stopped"
    final_error = ""

    try:
        if args.replay:
            return replay(args, mapper, link, log, stop, status_path)

        while not stop["flag"]:
            if deadline and time.monotonic() > deadline:
                break
            transport = getattr(link, "transport", None)
            if hasattr(transport, "check"):
                transport.check()
            # Release a held Ctrl even while HTTP polling is failing.
            mapper.watchdog()
            try:
                state = fetch_state(args.api, args.timeout)
                errors = 0
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                errors += 1
                if errors in (1, 10) or errors % 100 == 0:
                    print(f"api poll failed ({errors}): {exc}", file=sys.stderr)
                    log({"kind": "api_error", "error": str(exc), "count": errors})
                if status_path:
                    write_status(status_path, dict(t=time.time(), pid=os.getpid(),
                                 status="waiting_api", error=str(exc), device=device,
                                 generation=generation, link=check, counts=mapper.counts))
                time.sleep(min(2.0, args.poll * 5))
                continue

            result = state.get("result") or {}
            seq = state.get("result_seq")
            gated = []
            if state.get("gesture_output") != "pl_uart":
                gated.append("output_route_not_uart")
            if state.get("inference_active") is not True:
                gated.append("inference_inactive")
            if state.get("result_stale") is not False:
                gated.append("result_stale")
            age = state.get("result_age_s")
            if (isinstance(age, bool) or not isinstance(age, (int, float))
                    or not math.isfinite(age) or age < 0 or age > args.max_age):
                gated.append("too_old")
            if args.require_report and state.get("report_ready") is not True:
                gated.append("report_not_ready")
            if not isinstance(result, dict) or isinstance(seq, bool) or not isinstance(seq, int):
                gated.append("invalid_result")

            if seq != last_seq:
                last_seq = seq
                if result and not gated:
                    action = mapper.handle(result)
                    if action:
                        last_event = action
                elif isinstance(result, dict) and result:
                    log({"kind": "gated", "reasons": gated, "event": result.get("event")})

            if mapper.consecutive_errors >= args.max_consecutive_errors:
                # Exit on a broken link. A consumer restart only reattaches;
                # it never reloads the FPGA configuration.
                print(
                    f"link lost: {mapper.consecutive_errors} consecutive failures, exiting",
                    file=sys.stderr,
                )
                log({"kind": "link_lost", "consecutive": mapper.consecutive_errors})
                final_status = "error"
                final_error = "consecutive CH9329 failures"
                return 3

            now = time.monotonic()
            if status_path and now - last_status > args.status_interval:
                last_status = now
                write_status(
                    status_path,
                    {
                        "t": round(time.time(), 3),
                        "pid": os.getpid(),
                        "status": "ready",
                        "generation": generation,
                        "link": check,
                        "uptime_s": round(now - started, 1),
                        "device": device,
                        "phase": state.get("phase"),
                        "workflow_state": state.get("workflow_state"),
                        "gesture_output": state.get("gesture_output"),
                        "inference_active": state.get("inference_active"),
                        "result_seq": seq,
                        "last_event": last_event,
                        "gated": gated,
                        "counts": mapper.counts,
                        "consecutive_errors": mapper.consecutive_errors,
                        "link_stats": getattr(getattr(link, "transport", None), "stats", {}),
                        "ctrl_held": mapper.ctrl_held,
                        "min_scroll_px": mapper.min_scroll_px,
                    },
                )

            time.sleep(args.poll)
    except BaseException as exc:
        final_status = "error"
        final_error = str(exc)
        raise
    finally:
        try:
            mapper.release("shutdown")
            if hasattr(link, "release_all"):
                link.release_all()
        except Exception as exc:
            print(f"release on shutdown failed: {exc}", file=sys.stderr)
        log({"kind": "stop", "counts": mapper.counts})
        if status_path:
            write_status(status_path, dict(t=time.time(), pid=os.getpid(),
                         status=final_status, error=final_error, device=device,
                         generation=generation, link=check, counts=mapper.counts))

    print(json.dumps({"counts": mapper.counts}, ensure_ascii=False))
    return 0


def replay(args, mapper, link, log, stop, status_path) -> int:
    """Feed recorded gesture results instead of polling the web service."""
    path = Path(args.replay)
    print(f"replay {path}")
    delay = args.replay_delay
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if stop["flag"]:
                break
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            result = item.get("result", item)
            if not isinstance(result, dict):
                continue
            mapper.handle(result)
            if delay:
                time.sleep(delay)
    mapper.release("replay end")
    print(json.dumps({"counts": mapper.counts}, ensure_ascii=False))
    log({"kind": "replay_done", "counts": mapper.counts})
    return 0


def write_status(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=DEFAULT_API, help="camera_web state URL")
    p.add_argument("--poll", type=float, default=0.2, help="state poll period, seconds")
    p.add_argument("--duration", type=float, default=0.0, help="stop after N seconds (0 = forever)")
    p.add_argument("--overlay", default="", help="obsolete; use pynq-fpga.service")
    p.add_argument("--overlay-method", default="auto", choices=["auto", "pynq", "fpga_manager"])
    p.add_argument("--set-clock", type=float, default=0.0,
                   help="obsolete; clock configuration belongs to pynq-fpga.service")
    p.add_argument("--transport", default="uartlite", choices=["uartlite", "serial"])
    p.add_argument("--base", type=lambda v: int(v, 0), default=0x41200000, help="UARTLite base address")
    p.add_argument("--clock-hz", type=int, default=62_500_000, help="shared design UART clock (fixed)")
    p.add_argument("--device", default="/dev/ttyUSB0", help="serial device for --transport serial")
    p.add_argument("--baud", type=int, default=9600, help="CH9329 baud (factory default 9600)")
    p.add_argument("--timeout", type=float, default=0.35, help="answer timeout, seconds")
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--max-age", type=float, default=3.0, help="drop results older than this")
    p.add_argument("--require-report", dest="require_report", action="store_true", default=True,
                   help="only act after the eye report is ready (default)")
    p.add_argument("--no-require-report", dest="require_report", action="store_false")
    p.add_argument("--scroll-gain", type=float, default=0.032, help="wheel notches per gesture px")
    p.add_argument("--zoom-step", type=float, default=0.05, help="log-zoom per wheel notch")
    p.add_argument("--max-step", type=int, default=3, help="max notches in one report")
    p.add_argument("--min-scroll-px", type=float, default=2.0)
    p.add_argument("--invert-wheel", action="store_true", help="flip scroll/zoom direction")
    p.add_argument("--ctrl-timeout", type=float, default=3.0, help="Ctrl lease, seconds")
    p.add_argument("--max-consecutive-errors", type=int, default=8,
                   help="exit and reattach to the shared configuration after N failures")
    p.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--status-file", default="")
    p.add_argument("--status-interval", type=float, default=2.0)
    p.add_argument("--dry-run", action="store_true", help="print frames instead of sending")
    p.add_argument("--skip-link-check", action="store_true", help="obsolete; link verification is required")
    p.add_argument("--query-only", action="store_true",
                   help="only query CH9329 information; send no mouse/keyboard reports")
    p.add_argument("--replay", default="", help="JSONL of recorded results instead of /api/state")
    p.add_argument("--replay-delay", type=float, default=0.0)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.status_file:
        args.status_file = str(Path(args.log_dir) / "status.json")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
