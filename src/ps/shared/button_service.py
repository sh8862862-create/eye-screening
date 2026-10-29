#!/usr/bin/env python3
"""PYNQ-Z2 physical-button bridge for the eye-screening web app.

BTN0: run the voice questionnaire, then start the B-eye camera workflow.
BTN1: capture when the B-eye preview is fresh.
BTN2: stop inspection workers and restore only an idle web control service.
BTN3: confirm the current eye photo and start analysis.

This runs as a separate process.  BTN0/BTN1 use the existing CSRF-protected
camera_web HTTP API; BTN2 restarts ``pynq-camera-web.service`` so systemd's
control-group cleanup terminates any capture/analysis child processes.

It deliberately does not import or modify camera_web internals.  A broken
button service therefore cannot take down the web application, and BTN2 does
not depend on the web app being responsive.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib import error, request

LOG = logging.getLogger("pynq-buttons")

# Windows lacks SIGKILL; the board (Linux) uses SIGKILL.  SIGTERM is only a
# test/portability fallback and still causes systemd to restart the unit.
FORCE_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class PynqButtonReader:
    """Attach to the manager's input-only AXI GPIO; never configure the PL."""

    def __init__(self):
        from shared_fpga import SharedLease
        from pynq import MMIO

        self._lease = SharedLease("buttons")
        self._mmio = None
        try:
            self._lease.check()
            info = self._lease.descriptor
            self._mmio = MMIO(info["phys_addr"], info["addr_range"])
        except BaseException:
            self._lease.close()
            raise

    @property
    def generation(self):
        return self._lease.generation

    def read_mask(self) -> int:
        self._lease.check()
        return int(self._mmio.read(0)) & 0b1111

    def close(self) -> None:
        self._mmio = None
        self._lease.close()


class ButtonMonitor:
    """Debounced rising-edge detector for the four buttons."""

    def __init__(self, read_mask, on_press, *, debounce_s=.04, poll_s=.02,
                 clock=time.monotonic):
        self.read_mask = read_mask
        self.on_press = on_press
        self.debounce_s = float(debounce_s)
        self.poll_s = float(poll_s)
        self.clock = clock
        self._raw = 0
        self._stable = 0
        self._changed_at = 0.0
        self._initialized = False

    def poll(self, now: float | None = None) -> list[int]:
        now = self.clock() if now is None else float(now)
        raw = int(self.read_mask()) & 0b1111
        if not self._initialized:
            # A key held while the service starts is not a new user action.
            # It must be released and pressed again before it can trigger.
            self._raw = self._stable = raw
            self._changed_at = now
            self._initialized = True
            return []
        if raw != self._raw:
            self._raw = raw
            self._changed_at = now
        if now - self._changed_at < self.debounce_s:
            return []
        if raw == self._stable:
            return []
        pressed = raw & ~self._stable
        self._stable = raw
        return [bit for bit in range(4) if pressed & (1 << bit)]

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(self.poll_s):
            # A failed lease/read is fatal. Retrying an old MMIO handle could
            # touch a different design after an external reconfiguration.
            pressed = self.poll()
            for bit in pressed:
                try:
                    self.on_press(bit)
                except Exception:
                    LOG.exception("button action failed: BTN%s", bit)


class CameraWebClient:
    """Small client for the existing camera_web API."""

    def __init__(self, base_url: str, token_file: str | Path, timeout: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.token_file = Path(token_file)
        self.timeout = float(timeout)
        # Do not let HTTP_PROXY/HTTPS_PROXY intercept a local board request.
        self._opener = request.build_opener(request.ProxyHandler({}))

    def _token(self) -> str:
        try:
            token = self.token_file.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise RuntimeError("cannot read CSRF token file: %s" % exc) from exc
        if not token:
            raise RuntimeError("CSRF token file is empty")
        return token

    def _open(self, req: request.Request) -> tuple[int, bytes]:
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                return int(response.status), response.read()
        except error.HTTPError as exc:
            return int(exc.code), exc.read()
        except (error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("camera_web request failed: %s" % exc) from exc

    def state(self) -> dict:
        req = request.Request(self.base_url + "/api/state", method="GET")
        status, body = self._open(req)
        if status != 200:
            raise RuntimeError("camera_web state returned HTTP %d: %r" % (status, body[:200]))
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("camera_web state is not valid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("camera_web state is not an object")
        return value

    def post_action(self, action: str, **fields) -> tuple[int, bytes]:
        payload = json.dumps(dict(fields, action=action), separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": self._token(),
            "Origin": self.base_url,
        }
        req = request.Request(self.base_url + "/api/action", data=payload,
                              headers=headers, method="POST")
        return self._open(req)


class SystemctlAborter:
    """Restart camera_web so systemd's cgroup cleanup stops all children."""

    def __init__(self, command: list[str], timeout: float = 30.0,
                 kill_service: str | None = None):
        if not command:
            raise ValueError("restart command is empty")
        self.command = list(command)
        self.timeout = float(timeout)
        self.kill_service = kill_service

    def abort(self) -> None:
        if self.kill_service:
            # Include nested OpenCV/model subprocesses, not just MainPID.
            killed = subprocess.run(
                ["systemctl", "kill", "--kill-who=all", "--signal=SIGKILL",
                 self.kill_service], capture_output=True, text=True,
                timeout=self.timeout, check=False)
            if killed.returncode:
                LOG.warning("cgroup kill unavailable; using service restart cleanup: %s",
                            (killed.stderr or killed.stdout).strip()[:300])
        LOG.warning("BTN2: restarting camera web service: %s", " ".join(self.command))
        try:
            result = subprocess.run(self.command, capture_output=True, text=True,
                                    timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("restart command failed: %s" % exc) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError("restart command exited %d: %s" %
                               (result.returncode, message[:300]))
        LOG.info("camera web service restarted")


class ServiceProcessAborter:
    """No-sudo abort: kill camera_web's MainPID and let systemd restart it.

    The deployed unit has ``Restart=on-failure`` and
    ``KillMode=control-group``, so SIGKILL of the main process makes systemd
    stop the remaining cgroup (camera/analysis children) and start a fresh
    service.  This is the fallback when passwordless sudo is not configured.
    """

    def __init__(self, service: str = "pynq-camera-web.service",
                 wait_s: float = 30.0):
        self.service = service
        self.wait_s = float(wait_s)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=5, check=False)

    def _main_pid(self) -> int:
        result = self._run(["systemctl", "show", "-p", "MainPID",
                            "--value", self.service])
        if result.returncode != 0:
            raise RuntimeError("systemctl show failed: %s" %
                               (result.stderr or result.stdout).strip()[:300])
        value = (result.stdout or "").strip()
        try:
            return int(value)
        except ValueError as exc:
            raise RuntimeError("invalid MainPID: %r" % value) from exc

    def _active(self) -> bool:
        result = self._run(["systemctl", "is-active", self.service])
        return (result.stdout or "").strip() == "active"

    def abort(self) -> None:
        old_pid = self._main_pid()
        if old_pid <= 1:
            raise RuntimeError("camera web service has no live MainPID")
        LOG.warning("BTN2: killing camera web MainPID %s; systemd will restart it",
                    old_pid)
        os.kill(old_pid, FORCE_KILL)
        deadline = time.monotonic() + self.wait_s
        while time.monotonic() < deadline:
            time.sleep(.5)
            try:
                new_pid = self._main_pid()
            except Exception:
                continue
            if new_pid not in (0, old_pid) and self._active():
                LOG.info("camera web restarted as MainPID %s", new_pid)
                return
        raise RuntimeError("camera web did not restart within %.1fs" % self.wait_s)


class ButtonActions:
    """Map button numbers to existing camera_web actions."""

    def __init__(self, client: CameraWebClient, aborter: SystemctlAborter,
                 process_aborter: ServiceProcessAborter | None = None,
                 token_file: str | Path | None = None, *, voice_options=None):
        self.client = client
        self.aborter = aborter
        self.process_aborter = process_aborter
        self.token_file = Path(token_file) if token_file else None
        self._voice_thread = None
        self._voice_stop = None
        self._voice_lock = threading.Lock()
        self._shutdown = False
        self.voice_options = dict(voice_options or {})

    def __call__(self, bit: int) -> None:
        if bit == 0:
            self._start()
        elif bit == 1:
            self._capture()
        elif bit == 2:
            self._abort()
        elif bit == 3:
            self._analyze()
        else:
            LOG.warning("ignoring unknown button BTN%s", bit)

    def _state(self) -> dict | None:
        try:
            return self.client.state()
        except Exception as exc:
            LOG.warning("cannot read camera state; button ignored: %s", exc)
            return None

    def _start(self) -> None:
        state = self._state()
        if state is None:
            return
        if state.get("busy") or state.get("session_id"):
            LOG.warning("BTN0 ignored: a check is already active or busy")
            return
        # Unit callers and maintenance tools that do not provide the board's
        # token path retain the original direct camera action.
        if self.token_file is None:
            self._post("eye", "BTN0 start")
            return
        with self._voice_lock:
            if self._shutdown:
                return
            if self._voice_thread is not None and self._voice_thread.is_alive():
                LOG.warning("BTN0 ignored: voice questionnaire is already active")
                return
            self._voice_stop = threading.Event()
            self._voice_thread = threading.Thread(
                target=self._run_voice_questionnaire,
                args=(self._voice_stop,),
                name="wonderecho-questionnaire", daemon=True)
            self._voice_thread.start()
        LOG.info("BTN0 accepted: WonderEcho questionnaire started")

    def _run_voice_questionnaire(self, stop=None) -> None:
        """Run the pre-recorded question flow, then open the eye camera."""
        stop = stop if stop is not None else self._voice_stop
        if stop is None:
            stop = threading.Event()
        result_path = ((self.token_file.parent if self.token_file else Path("data")) /
                       "voice_questionnaire.json")
        quiz = None
        try:
            from wonderecho import Questionnaire, WonderEcho
            if stop.is_set():
                return
            device = WonderEcho()
            try:
                quiz = Questionnaire(device, stop=stop, result_path=result_path,
                                     **self.voice_options)
                completed = quiz.run()
            finally:
                device.close()
            if quiz.red_flags:
                LOG.warning("voice questionnaire stopped on red flag: %s",
                            ",".join(quiz.red_flags))
            elif completed:
                # Another client may have started a check while the spoken
                # questions were running.  Recheck before the HTTP action.
                # Serialize this final action with BTN2's cancellation so a
                # delayed questionnaire cannot reopen the camera after abort.
                with self._voice_lock:
                    if self._shutdown or stop.is_set():
                        return
                    state = self._state()
                    if state is None or state.get("busy") or state.get("session_id"):
                        LOG.warning("voice complete; camera start skipped: state unavailable or busy")
                        return
                    if not stop.is_set():
                        self._post("eye", "voice questionnaire complete; BTN0 start")
            else:
                LOG.info("voice questionnaire incomplete; camera remains stopped")
        except Exception as exc:
            LOG.exception("WonderEcho questionnaire failed; camera remains stopped")
            try:
                value = dict(status="error", error=str(exc),
                             answers=dict(quiz.answers) if quiz is not None else {},
                             red_flags=list(quiz.red_flags) if quiz is not None else [],
                             updated_at=time.time())
                result_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = result_path.with_suffix(result_path.suffix + ".tmp")
                temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                temporary.replace(result_path)
            except Exception:
                LOG.exception("cannot publish voice questionnaire error")
        finally:
            with self._voice_lock:
                if self._voice_stop is stop:
                    self._voice_thread = None
                    self._voice_stop = None

    def close(self, timeout=5.0) -> None:
        """Cancel the questionnaire and let it release its I2C lease."""
        with self._voice_lock:
            self._shutdown = True
            if self._voice_stop is not None:
                self._voice_stop.set()
            thread = self._voice_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                LOG.error("voice thread did not stop within %.1fs", timeout)

    def _capture(self) -> None:
        state = self._state()
        if state is None:
            return
        ready = (
            state.get("role") == "eye"
            and state.get("camera_active") is True
            and state.get("frame_fresh") is True
            and state.get("busy") is False
            and not state.get("session_id")
        )
        if not ready:
            LOG.warning("BTN1 ignored: B-eye preview is not ready "
                        "(role=%r camera=%r fresh=%r busy=%r session=%r)",
                        state.get("role"), state.get("camera_active"),
                        state.get("frame_fresh"), state.get("busy"),
                        state.get("session_id"))
            return
        self._post("capture", "BTN1 capture")

    def _analyze(self) -> None:
        state = self._state()
        if state is None:
            return
        if (state.get("busy") is not False or not state.get("session_id")
                or state.get("workflow_state") != "AWAITING_IMAGE_REVIEW"):
            LOG.warning("BTN3 ignored: no idle photo awaiting confirmation")
            return
        # Pressing BTN3 is the operator's explicit confirmation, equivalent
        # to checking the web checkbox and clicking its analyze button.
        self._post("analyze", "BTN3 confirm and analyze", confirm_eye=True)

    def _abort(self) -> None:
        with self._voice_lock:
            if self._voice_stop is not None:
                self._voice_stop.set()
        LOG.info("BTN2: voice questionnaire cancellation requested")
        # Persist the stop before restarting the service. An HTTP end_session
        # only queues work (or is rejected while busy), so it cannot prevent
        # the replacement process from restoring the old session/camera.
        if self.token_file is not None:
            marker = self.token_file.parent / "inspection_stopped.json"
            temporary = marker.with_suffix(".json.tmp")
            temporary.write_text(json.dumps({"source": "BTN2", "at": time.time()}),
                                 encoding="utf-8")
            temporary.replace(marker)
            LOG.info("BTN2: stop persisted; terminating inspection processes")
        else:
            # Compatibility for callers outside the deployed board service.
            self.client.post_action("end_session")
        try:
            self.aborter.abort()
            return
        except Exception as exc:
            LOG.error("BTN2 restart failed: %s", exc)
        if self.process_aborter is not None:
            try:
                self.process_aborter.abort()
                return
            except Exception as exc:
                LOG.error("BTN2 process-level abort failed: %s", exc)
        # Last-resort fallback for a non-systemd launch.  It cannot interrupt
        # a busy capture/analysis job; that is exactly why restart/kill is primary.
        try:
            self._post("stop", "BTN2 fallback stop")
        except Exception:
            LOG.exception("BTN2 fallback stop failed")

    def _post(self, action: str, label: str, **fields) -> None:
        status, body = self.client.post_action(action, **fields)
        if 200 <= status < 300:
            LOG.info("%s accepted (HTTP %d)", label, status)
        else:
            LOG.warning("%s rejected (HTTP %d): %r", label, status, body[:300])


class DryRunActions:
    def __call__(self, bit: int) -> None:
        LOG.info("DRY RUN: BTN%s pressed", bit)


def acquire_lock(path: Path):
    """Prevent two button readers from acting on the same press."""
    import fcntl

    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("another button_service instance is running")
    handle.seek(0)
    handle.truncate()
    handle.write("%d\n" % os.getpid())
    handle.flush()
    return handle


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",
                        default=os.environ.get("PYNQ_BUTTON_BASE_URL",
                                               "http://192.168.2.99:8080"),
                        help="camera_web base URL")
    parser.add_argument("--token-file",
                        default=os.environ.get("PYNQ_BUTTON_TOKEN_FILE",
                                               "/home/xilinx/gesture_demo/camera_web/data/.csrf_token"),
                        help="camera_web CSRF token file")
    parser.add_argument("--bitfile", default="",
                        help="obsolete; FPGA configuration belongs to pynq-fpga.service")
    parser.add_argument("--debounce-ms", type=int, default=40)
    parser.add_argument("--poll-ms", type=int, default=20)
    parser.add_argument("--restart-command",
                        default=os.environ.get(
                            "PYNQ_BUTTON_RESTART",
                            "sudo -n systemctl restart pynq-camera-web.service"),
                        help="command used for BTN2 abort")
    parser.add_argument("--dry-run", action="store_true",
                        help="read buttons and log presses without HTTP/systemctl")
    parser.add_argument("--lock-file", default="",
                        help="lock file; default: data/buttons.lock next to the token")
    parser.add_argument("--status-file", default="",
                        help="default: data/buttons_status.json next to the token")
    parser.add_argument("--voice-prompt-delay", type=float, default=3.0,
                        help="seconds to wait for each question recording (default: 3)")
    parser.add_argument("--voice-answer-timeout", type=float, default=12.0,
                        help="seconds to wait for an answer after playback (default: 12)")
    parser.add_argument("--voice-repeat-count", type=int, default=1,
                        help="number of question retries after timeout (default: 1)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.voice_prompt_delay) or args.voice_prompt_delay < 0:
        parser.error("--voice-prompt-delay must be finite and nonnegative")
    if not math.isfinite(args.voice_answer_timeout) or args.voice_answer_timeout <= 0:
        parser.error("--voice-answer-timeout must be finite and positive")
    if args.voice_repeat_count < 0:
        parser.error("--voice-repeat-count must be nonnegative")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.bitfile:
        raise SystemExit("--bitfile is no longer supported; use pynq-fpga.service")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = None
    lock_path = (Path(args.lock_file) if args.lock_file
                 else Path(args.token_file).parent / "buttons.lock")
    try:
        lock = acquire_lock(lock_path)
    except RuntimeError as exc:
        LOG.warning("%s; exiting", exc)
        return 0
    except OSError:
        LOG.exception("cannot acquire lock file %s", lock_path)
        return 2

    try:
        reader = PynqButtonReader()
    except Exception:
        LOG.exception("cannot attach PYNQ buttons; check pynq-fpga.service")
        lock.close()
        return 2

    if args.dry_run:
        actions = DryRunActions()
    else:
        client = CameraWebClient(args.base_url, args.token_file, timeout=1.0)
        aborter = SystemctlAborter(shlex.split(args.restart_command),
                                  kill_service="pynq-camera-web.service")
        process_aborter = ServiceProcessAborter("pynq-camera-web.service")
        actions = ButtonActions(client, aborter, process_aborter,
                                token_file=args.token_file,
                                voice_options=dict(prompt_delay=args.voice_prompt_delay,
                                                   answer_timeout=args.voice_answer_timeout,
                                                   repeat_count=args.voice_repeat_count))

    status_path = (Path(args.status_file) if args.status_file
                   else Path(args.token_file).parent / "buttons_status.json")
    status = dict(status="initializing", pid=os.getpid(), generation=reader.generation,
                  mask=None, updated_at=None, error="", dry_run=args.dry_run)
    last_status = 0.0

    def publish(**changes):
        status.update(changes)
        status["updated_at"] = time.time()
        status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = status_path.with_name(status_path.name + ".tmp." + str(os.getpid()))
        temporary.write_text(json.dumps(status), encoding="utf-8")
        os.chmod(temporary, 0o644)
        temporary.replace(status_path)

    def read_mask():
        nonlocal last_status
        mask = reader.read_mask()
        now = time.monotonic()
        if now - last_status >= 1.0 or mask != status["mask"]:
            publish(status="ready", mask=mask, error="")
            last_status = now
        return mask

    monitor = ButtonMonitor(read_mask, actions,
                            debounce_s=max(0, args.debounce_ms) / 1000.0,
                            poll_s=max(5, args.poll_ms) / 1000.0)
    stop = threading.Event()

    def request_stop(signum, frame):
        LOG.info("signal %s received; stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    LOG.info("ready: BTN0=start, BTN1=capture, BTN2=abort, BTN3=analyze")
    result = 0
    try:
        publish()
        monitor.run(stop)
    except Exception as exc:
        LOG.exception("button monitor failed; detaching shared FPGA")
        publish(status="error", error=str(exc))
        result = 2
    finally:
        if isinstance(actions, ButtonActions):
            actions.close()
        reader.close()
        lock.close()
        if status["status"] != "error":
            publish(status="stopped")
    return result


if __name__ == "__main__":
    sys.exit(main())
