#!/usr/bin/env python3
"""WonderEcho I2C question playback for the PYNQ-Z2 screening workflow.

The module stores the question audio and answer vocabulary.  The board owns
the order: it writes a playback ID, then reads 0x64 until it gets one of the
three answer IDs.  WonderEcho uses the dedicated PMODB B9/B10 I2C
controller; ``SharedLease`` guards its lifetime and transactions.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path

LOG = logging.getLogger("wonderecho")

ADDRESS = 0x34
PLAY_REGISTER = 0x6E
RESULT_REGISTER = 0x64
YES, NO, UNSURE = 0x01, 0x02, 0x03
PROMPT_REPEAT = 0x20
PROMPT_DONE = 0x21
PROMPT_RED_FLAG = 0x22

# Ten-question short form. Keep original keys and firmware playback IDs so
# existing recordings/history retain their meaning. Omit Q2/Q4/Q8/Q10/Q11.
# R1-R3 remain first: run() uses these three positions for the stop condition.
QUESTIONS = (
    ("R1", 0x10), ("R2", 0x11), ("R3", 0x12),
    ("Q1", 0x13), ("Q3", 0x15), ("Q5", 0x17), ("Q6", 0x18),
    ("Q7", 0x19), ("Q9", 0x1B), ("Q12", 0x1E),
)


class WonderEcho:
    def __init__(self, lease=None, address=ADDRESS):
        if lease is None:
            import sys
            sys.path.insert(0, "/opt/pynq-shared")
            from shared_fpga import SharedLease
            lease = SharedLease("voice_iic")
        self.lease = lease
        try:
            self.iic = lease.iic
            self.address = int(address)
            if not 0 <= self.address <= 0x7F:
                raise ValueError("I2C address must fit seven bits")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def _write(self, payload: bytes):
        sent = self.iic.send(self.address, payload, len(payload))
        try:
            if sent != len(payload):
                raise RuntimeError("WonderEcho short I2C write %d/%d" %
                                   (sent, len(payload)))
        finally:
            self.iic.wait()

    def play(self, prompt_id: int):
        prompt_id = int(prompt_id)
        if not 0 <= prompt_id <= 0xFF:
            raise ValueError("prompt ID must fit one byte")
        self._write(bytes((PLAY_REGISTER, 0xFF, prompt_id)))

    def result(self) -> int:
        from cffi import FFI

        # AxiIIC forwards the receive pointer to CFFI without converting a
        # bytearray.  Supply writable C storage for XIic_Recv's unsigned char*.
        # WonderEcho exposes the answer as register 0x64, so select it before
        # the one-byte read (the module accepts a STOP/START pair here).
        self._write(bytes((RESULT_REGISTER,)))
        data = FFI().new("unsigned char[1]")
        received = self.iic.receive(self.address, data, 1)
        try:
            if received != 1:
                raise RuntimeError("WonderEcho short I2C read %s/1" % received)
        finally:
            self.iic.wait()
        return int(data[0])


class Questionnaire:
    """Cancellable board-side state machine for the pre-recorded questions."""

    def __init__(self, device: WonderEcho, *, stop=None, result_path=None,
                 prompt_delay=3.0, answer_timeout=12.0, repeat_count=1,
                 sleep=time.sleep, clock=time.monotonic):
        self.device = device
        self.stop = stop or threading.Event()
        self.prompt_delay = float(prompt_delay)
        self.answer_timeout = float(answer_timeout)
        self.repeat_count = int(repeat_count)
        if not math.isfinite(self.prompt_delay) or self.prompt_delay < 0:
            raise ValueError("prompt_delay must be finite and nonnegative")
        if not math.isfinite(self.answer_timeout) or self.answer_timeout <= 0:
            raise ValueError("answer_timeout must be finite and positive")
        if self.repeat_count < 0:
            raise ValueError("repeat_count must be nonnegative")
        self.sleep = sleep
        self.clock = clock
        self.result_path = Path(result_path) if result_path else None
        self.answers = {}
        self.red_flags = []

    def _stopped(self):
        return self.stop.is_set()

    def _save(self, status, **extra):
        if not self.result_path:
            return
        value = dict(status=status, answers=self.answers,
                     red_flags=self.red_flags, updated_at=time.time())
        value.update(extra)
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.result_path.with_suffix(self.result_path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.result_path)

    def _answer(self):
        deadline = self.clock() + self.answer_timeout
        while self.clock() < deadline and not self._stopped():
            value = self.device.result()
            # Reading may consume the recognition result.  In particular, the
            # first read and a repeated answer on consecutive questions count.
            if value in (YES, NO, UNSURE):
                return value
            self.sleep(0.05)
        return None

    def _pause(self, seconds):
        """Wait for playback without delaying BTN2/service cancellation."""
        deadline = self.clock() + seconds
        while not self._stopped():
            remaining = deadline - self.clock()
            if remaining <= 0:
                return True
            self.sleep(min(0.05, remaining))
        return False

    def _ask(self, prompt_id):
        if self._stopped():
            return None
        # Discard a pending result before starting a new prompt, never after
        # playback when the user may already have answered.
        self.device.result()
        if self._stopped():
            return None
        self.device.play(prompt_id)
        if not self._pause(self.prompt_delay):
            return None
        return self._answer()

    def run(self):
        self.answers = {}
        self.red_flags = []
        self._save("running")
        try:
            for index, (key, prompt_id) in enumerate(QUESTIONS):
                if self._stopped():
                    self._save("aborted")
                    return False
                self._save("running", question=key, question_index=index)
                value = self._ask(prompt_id)
                for _ in range(self.repeat_count):
                    if value is not None or self._stopped():
                        break
                    self.device.play(PROMPT_REPEAT)
                    if not self._pause(1.0):
                        break
                    value = self._ask(prompt_id)
                if self._stopped():
                    self._save("aborted", question=key, question_index=index)
                    return False
                answer = {YES: "yes", NO: "no", UNSURE: "unknown"}.get(value, "timeout")
                self.answers[key] = answer
                self._save("running", question=key, question_index=index,
                           last_answer=answer)
                if index < 3 and value == YES:
                    self.red_flags.append(key)
                    self._save("red_flag", question=key)
                    self.device.play(PROMPT_RED_FLAG)
                    return False
                if value is None:
                    self._save("timeout", question=key, question_index=index,
                               last_answer=answer)
                    return False
            if self._stopped():
                self._save("aborted")
                return False
            self.device.play(PROMPT_DONE)
            if self._stopped():
                self._save("aborted")
                return False
            self._save("completed")
            return True
        except Exception as exc:
            LOG.exception("WonderEcho questionnaire failed")
            self._save("error", error=str(exc))
            raise

