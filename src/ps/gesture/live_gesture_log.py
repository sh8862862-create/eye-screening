"""CPU gesture diagnostic, with optional events to the local PC test page."""
import argparse
import json
from pathlib import Path
import threading
import time

import cv2 as cv
import numpy as np

from mp_palmdet import MPPalmDet
from mp_handpose import MPHandPose
from gesture_control import GestureControl, features
from gesture_sender import GestureSender, publish


class LatestFrame:
    """Drain camera continuously so slow inference does not queue old frames."""
    def __init__(self, cap):
        self.cap = cap
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.frame = None
        self.timestamp = 0.
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while not self.stop_event.is_set():
                ok, frame = self.cap.read()
                if not ok:
                    raise RuntimeError("Camera read failed")
                with self.lock:
                    self.frame, self.timestamp = frame, time.monotonic()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            self.cap.release()

    def get(self):
        with self.lock:
            if self.error:
                raise RuntimeError(self.error)
            return (None if self.frame is None else self.frame.copy()), self.timestamp

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=2.)


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--rotate", type=int, choices=(0, 180), default=180)
    parser.add_argument("--send-to", help="PC test-page URL, e.g. http://192.168.2.1:8765; omit for log only")
    parser.add_argument("--capture-mode", choices=("read", "on-demand"), default="read",
                        help="read: original continuous conversion; on-demand: retrieve only for inference")
    parser.add_argument("--track-hand", action="store_true",
                        help="experimental: reuse hand ROI for two frames between palm detections")
    parser.add_argument("--hand-backend", choices=("cpu", "fpga-hybrid"), default="cpu",
                        help="handpose backend; cpu is the default until real-camera hybrid validation")
    parser.add_argument("--hybrid-overlay-dir", default="/home/xilinx/pynq_test",
                        help="PYNQ overlay directory used by --hand-backend fpga-hybrid")
    parser.add_argument("--hybrid-suffix-model",
                        help="CPU suffix ONNX path; defaults to gesture_demo/handpose_hybrid/handpose_suffix_node13.onnx")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    sender = None
    if args.send_to:
        try:
            sender = GestureSender(args.send_to)
            sender.send(GestureControl().reset("STOP_RELEASE"))
        except (OSError, ValueError) as exc:
            parser.error(f"PC connection failed: {exc}")
    cv.setNumThreads(2)
    cv.ocl.setUseOpenCL(False)
    palm = MPPalmDet(str(root / "gesture_models/palm_detection_mediapipe_2023feb.onnx"),
                     backendId=cv.dnn.DNN_BACKEND_OPENCV, targetId=cv.dnn.DNN_TARGET_CPU)
    if args.hand_backend == "cpu":
        pose = MPHandPose(str(root / "gesture_compat/handpose_opencv454.onnx"),
                          backendId=cv.dnn.DNN_BACKEND_OPENCV, targetId=cv.dnn.DNN_TARGET_CPU)
    else:
        from mp_handpose_hybrid import MPHandPoseHybrid
        default_suffix = root / "handpose_hybrid/handpose_suffix_node13.onnx"
        if not default_suffix.is_file():
            default_suffix = root.parent / "handpose_hybrid/handpose_suffix_node13.onnx"
        suffix_model = args.hybrid_suffix_model or str(default_suffix)
        pose = MPHandPoseHybrid(
            suffix_model,
            args.hybrid_overlay_dir,
            backendId=cv.dnn.DNN_BACKEND_OPENCV,
            targetId=cv.dnn.DNN_TARGET_CPU,
        )
    pose.PALM_BOX_PRE_ENLARGE_FACTOR = 6
    pose.PALM_BOX_ENLARGE_FACTOR = 4
    tracker = None
    if args.track_hand:
        from hand_tracker import HandTracker
        tracker = HandTracker(palm, pose)
    if args.capture_mode == "on-demand":
        from capture_on_demand import OnDemandFrame
        camera = OnDemandFrame(args.camera)
    else:
        cap = cv.VideoCapture(args.camera, cv.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError("Cannot open camera; check video group and camera ownership")
        cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
        camera = LatestFrame(cap)
    control = GestureControl()
    log_dir = root / "gesture_logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / (time.strftime("gesture_%Y%m%d_%H%M%S") + "_" + str(time.time_ns()) + ".jsonl")
    print(f"PC PAGE OUTPUT: {args.send_to}; Ctrl+C stops." if sender else
          "LOG ONLY: no mouse/keyboard/network output. Ctrl+C stops.", flush=True)
    print("Pinch and hold: activate/deactivate; release pinch before the next action.", flush=True)
    print("Index only: scroll; open palm and hold: zoom; index only and hold: return to scroll.", flush=True)
    print("CPU is slow: hold poses about 10 seconds for this diagnostic.", flush=True)
    print(f"Capture mode: {args.capture_mode}; ms=inference, cycle_ms=full iteration interval", flush=True)
    print(f"Hand ROI tracking: {args.track_hand}; hand backend: {args.hand_backend}", flush=True)
    print("LOG:", log_path, flush=True)
    started = time.monotonic()
    last_completed = started
    previous_frame_time = 0.
    try:
        with log_path.open("w", encoding="utf-8") as log:
            while time.monotonic() - started < args.seconds:
                frame, captured_at = camera.get()
                now = time.monotonic()
                if frame is None or captured_at <= previous_frame_time:
                    if now - max(started, captured_at) > 3:
                        raise RuntimeError("No fresh camera frame for 3 seconds")
                    time.sleep(.02)
                    continue
                previous_frame_time = captured_at
                if now - captured_at > 1:
                    raise RuntimeError("Camera frame is stale")
                if args.rotate == 180:
                    frame = cv.rotate(frame, cv.ROTATE_180)
                t0 = time.monotonic()
                f = None
                issue = ""
                hand = None
                source = "DETECT"
                if args.hand_backend == "fpga-hybrid":
                    pose.last_fpga_seconds = pose.last_suffix_seconds = None
                if tracker:
                    hand, source, issue = tracker.infer(frame, captured_at)
                else:
                    palms = palm.infer(frame)
                    if len(palms) == 1 and np.isfinite(palms).all():
                        hand = pose.infer(frame, palms[0].copy())
                    elif len(palms) > 1:
                        issue = "Multiple hands: release; show one hand only"
                if hand is not None:
                    try:
                        f = features(hand[4:67].reshape(21, 3))
                    except ValueError as exc:
                        issue = str(exc)
                        if tracker:
                            tracker.reset()
                done = time.monotonic()
                if done - captured_at > 5:
                    f, issue = None, "Result over 5 seconds old: release"
                    if tracker:
                        tracker.reset()
                result = control.update(f, captured_at, reacquired=source == "REDETECT")
                attempted_event = result["event"]
                result, delivery, delivery_error = publish(sender, control, result, captured_at)
                if delivery_error:
                    issue = (issue + " " + delivery_error).strip()
                completed = time.monotonic()
                record = {"elapsed_s": round(done - started, 3),
                          "pipeline_ms": round((done - t0) * 1000, 1),
                          "cycle_ms": round((completed - last_completed) * 1000, 1),
                          "hand_backend": args.hand_backend,
                          "fpga_frontend_ms": (None if args.hand_backend == "cpu" or pose.last_fpga_seconds is None
                                                else round(pose.last_fpga_seconds * 1000, 3)),
                          "cpu_suffix_ms": (None if args.hand_backend == "cpu" or pose.last_suffix_seconds is None
                                             else round(pose.last_suffix_seconds * 1000, 3)),
                          "input_age_ms": round((t0 - captured_at) * 1000, 1),
                          "capture_mode": args.capture_mode,
                          "hand_source": source, "track_hand": args.track_hand,
                          "result_age_ms": round((done - captured_at) * 1000, 1),
                          "rotate": args.rotate, "features": f, "issue": issue,
                          "delivery": delivery, "attempted_event": attempted_event, **result}
                last_completed = completed
                if args.capture_mode == "on-demand":
                    record["capture_counts"] = camera.stats()
                log.write(json.dumps(record, allow_nan=False) + "\n")
                log.flush()
                info = "no landmarks" if f is None else (
                    f"pinch={f['pinch_ratio']:.2f} index_only={int(f['pointing'])} "
                    f"open={int(f['open_palm'])} y8={f['index_y']:.1f}")
                print(f"{result['mode']:6} {result['event']:18} {info} "
                      f"scroll={result['scroll_px']:+.1f} zoom={result['zoom_factor']:.3f} "
                      f"ms={record['pipeline_ms']:.0f} cycle_ms={record['cycle_ms']:.0f} src={source} net={delivery} {issue}", flush=True)
    except KeyboardInterrupt:
        print("Stopped by Ctrl+C", flush=True)
    finally:
        release = control.reset("STOP_RELEASE")
        _, delivery, error = publish(sender, control, release, None)
        if sender:
            print(f"Final release: {delivery} {error}", flush=True)
        camera.close()
        if args.hand_backend == "fpga-hybrid":
            pose.close()
        if getattr(camera, 'forced_stop', False):
            print("Camera worker was blocked; stopped and reaped the camera process.", flush=True)
        print("Stopped; state cleared. LOG:", log_path, flush=True)


if __name__ == "__main__":
    main()
