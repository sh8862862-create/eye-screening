"""Isolated camera/model workers with opt-in USART-HID bridge output."""
import queue
import sys
import time
from pathlib import Path


def latest_put(q, item):
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        return
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def preview_put(q, item):
    # The camera must never read its own multiprocessing pipe. Reading a
    # partially written JPEG can block even with get_nowait(), preventing
    # shutdown when the supervisor is busy stopping inference. The small
    # bounded queue is drained by the supervisor; discard overflow frames.
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def camera_worker(device, rotation, raw, frame_lock, stamp, seq, released, stop, output, fast_preview=False, jpeg_size=None):
    import cv2 as cv
    import numpy as np
    output.cancel_join_thread()
    cv.setNumThreads(1)
    cv.ocl.setUseOpenCL(False)
    cap = None
    failed = False
    try:
        cap = cv.VideoCapture(device, cv.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError('摄像头无法打开，请检查 USB 接线或其他程序占用。')
        cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*('MJPG' if fast_preview else 'YUYV')))
        cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
        # Some UVC cameras negotiate this request back to 30 fps. Drain every
        # raw frame, but only convert the frames needed by the 5 fps preview.
        requested_fps = 120 if fast_preview else 10
        fps_set = cap.set(cv.CAP_PROP_FPS, requested_fps)
        print('camera_config requested_fps=%s set_ok=%s reported_fps=%s' %
              (requested_fps, bool(fps_set), cap.get(cv.CAP_PROP_FPS)), flush=True)
        shared = np.frombuffer(raw, dtype=np.uint8).reshape(480, 640, 3)
        last_encode = None
        last_decode = None
        preview_number = 0
        native_jpeg = False
        if fast_preview:
            cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
            native_jpeg = bool(cap.set(cv.CAP_PROP_CONVERT_RGB, 0))
            print('camera_preview native_jpeg=%s' % native_jpeg, flush=True)
        while not stop.is_set():
            ok = cap.grab()
            captured = time.monotonic()
            if not ok:
                raise RuntimeError('摄像头读取失败。')
            if not fast_preview and last_encode is not None and captured - last_encode < .2:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                raise RuntimeError('摄像头读取失败。')
            if native_jpeg:
                image = frame.tobytes()
                if image[:2] != b'\xff\xd8':
                    # Some backends accept the option but still return BGR.
                    native_jpeg = False
                    cap.set(cv.CAP_PROP_CONVERT_RGB, 1)
                    continue
                preview_number += 1
                if jpeg_size is not None:
                    # Keep the capture path entirely compressed. Only the
                    # inference process decodes the newest JPEG on demand.
                    shared_bytes = shared.reshape(-1)
                    if len(image) > shared_bytes.size:
                        raise RuntimeError('相机 JPEG 超出共享缓冲区大小。')
                    with frame_lock:
                        shared_bytes[:len(image)] = frame.reshape(-1)
                        jpeg_size.value = len(image)
                        stamp.value = captured
                        seq.value = preview_number
                preview_put(output, dict(kind='frame', image=image, at=captured,
                                       seq=preview_number, rotation=rotation,
                                       transport='native-mjpeg'))
                if jpeg_size is not None:
                    continue
                # CPU inference needs fresh BGR, not every high-rate preview
                # frame. Preserve its original 5 Hz input and rotation.
                if last_decode is not None and captured - last_decode < .2:
                    continue
                frame = cv.imdecode(frame, cv.IMREAD_COLOR)
                if frame is None:
                    raise RuntimeError('相机 MJPEG 解码失败。')
                last_decode = captured
            if frame.shape != (480, 640, 3):
                raise RuntimeError('摄像头未返回预期的 640×480 彩色画面。')
            if rotation == 180:
                frame = cv.rotate(frame, cv.ROTATE_180)
            with frame_lock:
                shared[:] = frame
                if jpeg_size is not None:
                    jpeg_size.value = 0
                stamp.value = captured
                seq.value += 1
                number = seq.value
            if native_jpeg:
                continue
            ok, jpg = cv.imencode('.jpg', frame, [cv.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                raise RuntimeError('画面编码失败。')
            latest_put(output, dict(kind='frame', image=jpg.tobytes(), at=captured, seq=number))
            last_encode = captured
    except BaseException as exc:
        failed = True
        latest_put(output, dict(kind='error', error=str(exc)))
    finally:
        if cap is not None:
            cap.release()
            if cap.isOpened():
                failed = True
            else:
                released.value = 1
        else:
            released.value = 1
    if failed:
        sys.exit(2)


def inference_worker(root, raw, frame_lock, stamp, seq, stop, output,
                     hid_serial_device=None, hid_serial_baud=115200, jpeg_size=None, rotation=0):
    import cv2 as cv
    import numpy as np
    sys.path.insert(0, str(Path(root) / 'gesture_image_test'))
    from mp_palmdet import MPPalmDet
    from mp_handpose import MPHandPose
    from gesture_control import GestureControl, features
    from hand_tracker import HandTracker
    bridge = None
    output.cancel_join_thread()
    cv.setNumThreads(2)
    cv.ocl.setUseOpenCL(False)
    try:
        palm = MPPalmDet(str(Path(root) / 'gesture_models/palm_detection_mediapipe_2023feb.onnx'),
                         backendId=cv.dnn.DNN_BACKEND_OPENCV, targetId=cv.dnn.DNN_TARGET_CPU)
        # Only the helper owns the FPGA lease/DMA. This worker stays unprivileged.
        import os
        if os.environ.get('PYNQ_PALM_PL') == '1':
            sys.path.insert(0, '/opt/pynq-palm')
            from palm_rpc import PalmNetProxy
            palm.model = PalmNetProxy(palm.model)
        pose = MPHandPose(str(Path(root) / 'gesture_compat/handpose_opencv454.onnx'),
                          backendId=cv.dnn.DNN_BACKEND_OPENCV, targetId=cv.dnn.DNN_TARGET_CPU)
        pose.PALM_BOX_PRE_ENLARGE_FACTOR = 6
        pose.PALM_BOX_ENLARGE_FACTOR = 4
        # The web path uses the same opt-in ROI tracking that was validated by
        # the command-line gesture test.  The camera worker still drains the
        # UVC stream continuously, while inference only works on the newest
        # frame and performs palm detection again after a short track window.
        # Palm detection is roughly 2.6x slower than a tracked hand-pose pass
        # on the PYNQ-Z2 CPU. Keep the ROI for five consecutive frames before
        # a full-frame palm search; HandTracker still resets immediately when
        # confidence, motion, or image bounds become invalid.
        # A full palm pass can take 4-5 seconds under live camera load.
        # The old four-second gap then discarded every valid ROI before
        # its first TRACK pass. Match the controller's six-second reset
        # window; confidence, geometry and result freshness checks stay on.
        tracker = HandTracker(palm, pose, max_tracks=5, max_gap=6.)
        control = GestureControl()
        if hid_serial_device:
            from hid_serial import SerialMouseBridge
            bridge = SerialMouseBridge(hid_serial_device, hid_serial_baud)
        previous = 0
        while not stop.is_set():
            if not frame_lock.acquire(timeout=1):
                raise RuntimeError('采集缓冲区锁定超时。')
            try:
                number, captured = seq.value, stamp.value
                if number > previous:
                    size = jpeg_size.value if jpeg_size is not None else 0
                    if size:
                        frame = np.frombuffer(raw, dtype=np.uint8, count=size).copy()
                    else:
                        frame = np.frombuffer(raw, dtype=np.uint8).reshape(480, 640, 3).copy()
            finally:
                frame_lock.release()
            if number <= previous:
                stop.wait(.05)
                continue
            previous = number
            if size:
                frame = cv.imdecode(frame, cv.IMREAD_COLOR)
                if frame is None or frame.shape != (480, 640, 3):
                    raise RuntimeError('相机 JPEG 解码未返回预期的 640×480 彩色图像。')
                if rotation == 180:
                    frame = cv.rotate(frame, cv.ROTATE_180)
            if time.monotonic() - captured > 1:
                control.suspend('INPUT_STALE')
                continue
            t0 = time.monotonic()
            hand, f, issue = None, None, ''
            hand, source, issue = tracker.infer(frame, captured)
            if hand is not None:
                if float(hand[131]) >= .8:
                    try:
                        f = features(hand[4:67].reshape(21, 3))
                    except ValueError as exc:
                        issue = str(exc)
                        tracker.reset()
                else:
                    issue = '手势置信度不足，暂不触发控制'
            done = time.monotonic()
            if done - captured > 7:
                f, issue = None, '本帧识别超过 7 秒，跳过移动并继续识别，保留控制模式'
                tracker.reset()
            result = control.update(f, captured, reacquired=source == 'REDETECT')
            delivery = 'WEB_VIEW_ONLY'
            if bridge is not None and bridge.send(result, captured):
                delivery = 'SERIAL_HID_BRIDGE'
            cv.rectangle(frame, (0, 0), (640, 30), (15, 25, 35), -1)
            cv.putText(frame, result['mode'] + ' / ' + result['event'], (10, 21),
                       cv.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
            ok, jpg = cv.imencode('.jpg', frame, [cv.IMWRITE_JPEG_QUALITY, 80])
            record = dict(kind='result', at=captured, completed=done, seq=number,
                          pipeline_ms=round((done-t0)*1000, 1),
                          result_age_ms=round((done-captured)*1000, 1),
                          features=f, issue=issue, delivery=delivery,
                          capture_mode='latest-frame-on-demand', track_hand=True,
                          palm_backend=getattr(palm.model, 'backend_name', 'cpu'),
                          palm_backend_error=getattr(palm.model, 'failure', ''),
                          hand_source=source, hand_detected=(hand is not None), **result)
            if ok:
                record['image'] = jpg.tobytes()
            latest_put(output, record)
    except BaseException as exc:
        latest_put(output, dict(kind='error', error='手势识别停止：' + str(exc)))
        sys.exit(2)
    finally:
        if bridge is not None:
            bridge.close()


def workflow_worker(switch_root, session, config, action, output):
    sys.path.insert(0, switch_root)
    from staged_workflow import capture_eye, analyze_eye
    try:
        if action == 'capture':
            record = capture_eye(Path(session), config)
        else:
            record = analyze_eye(Path(session), True, 'cpu')
        output.put(dict(ok=True, record=record))
    except BaseException as exc:
        output.put(dict(ok=False, error=str(exc) or type(exc).__name__))
        sys.exit(2)
