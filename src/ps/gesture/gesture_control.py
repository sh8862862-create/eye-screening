"""Gesture state machine for diagnostic logs only; no OS input or networking."""
import math
import numpy as np


def features(landmarks):
    p = np.asarray(landmarks, dtype=float)
    if p.shape != (21, 3) or not np.isfinite(p).all():
        raise ValueError("Expected 21 finite XYZ landmarks")
    p = p[:, :2]
    distance = lambda a, b: float(np.linalg.norm(p[a] - p[b]))
    width = distance(5, 17)
    if width < 15:
        raise ValueError("Palm too small for this diagnostic")
    def angle(a, b, c):
        u, v = p[a] - p[b], p[c] - p[b]
        denom = np.linalg.norm(u) * np.linalg.norm(v)
        return math.degrees(math.acos(float(np.clip(np.dot(u, v) / max(denom, 1e-6), -1, 1))))
    fingers = []
    for base in (5, 9, 13, 17):
        bend = angle(base, base + 1, base + 3)
        reach = distance(0, base + 3) / max(distance(0, base + 1), 1e-6)
        fingers.append("extended" if bend >= 155 and reach >= 1.12 else
                       "folded" if bend <= 125 or reach <= 1.05 else "uncertain")
    pointing = fingers == ["extended", "folded", "folded", "folded"]
    thumb_open = angle(1, 2, 4) >= 145 and distance(4, 5) / width >= .6
    # A tucked thumb may rest across the fist OR lower on the palm. Test
    # the palm's own coordinate frame, independent of image rotation/scale.
    # A thumb extended out to the side remains outside this bounded region.
    across = p[17] - p[5]
    along = np.mean(p[[5, 9, 13, 17]], axis=0) - p[0]
    basis = np.column_stack((across, along))
    palm_area = abs(float(np.linalg.det(basis))) / (width * width)
    thumb_in_palm = False
    thumb_position = None
    if palm_area >= .15:
        u, v = np.linalg.solve(basis, p[4] - p[0])
        # Wrist is not necessarily aligned with the index knuckle.
        index_u = np.linalg.solve(basis, p[5] - p[0])[0]
        thumb_position = [round(float(u - index_u), 3), round(float(v), 3)]
        thumb_in_palm = -.15 <= u - index_u <= 1.15 and .25 <= v <= 1.15
    thumb_folded = bool(distance(4, 9) / width <= .80 or
                       distance(4, 13) / width <= .65 or thumb_in_palm)
    index_scroll_pose = thumb_folded and fingers[1:] == ["folded"] * 3
    index_direction = ("up" if fingers[0] == "extended" else
                       "down" if fingers[0] == "folded" else None) if index_scroll_pose else None
    pointing = pointing and thumb_folded
    open_palm = all(f == "extended" for f in fingers) and thumb_open
    return {"pinch_ratio": distance(4, 8) / width, "palm_width": width,
            "index_y": float(p[8, 1]), "pointing": pointing,
            "open_palm": open_palm, "fingers": fingers,
            "thumb_folded": bool(thumb_folded), "thumb_palm_position": thumb_position,
            "index_scroll_pose": bool(index_scroll_pose),
            "index_scroll_direction": index_direction,
            "index_bend_deg": round(angle(5, 6, 8), 1)}


class GestureControl:
    """Two confirmed observations are required for activation/mode changes.

    Confirmation requires two fresh observations spanning at least 350 ms.
    Positive scroll_px means page content moves UP. Zoom uses tip distance
    divided by palm width, so uniform approach to the camera cancels out.
    """
    def __init__(self):
        self.mode = "IDLE"
        self.last_time = None
        self.pinch_down = False
        self.pinch_latched = False
        self.candidate = None
        self.candidate_start = 0.
        self.candidate_count = 0
        self.scroll_y = None
        self.scroll_accum = 0.
        self.scroll_direction = None
        self.scroll_direction_count = 0
        self.last_scroll_at = None
        self.zoom_ratio = None
        self.zoom_accum = 0.
        self.auto_pointing_enabled = True
        self.tracking_missing = False
        self.scroll_baseline_needed = False

    def clear_motion(self):
        self.scroll_direction = None
        self.scroll_direction_count = 0
        self.last_scroll_at = None
        self.scroll_y = None
        self.scroll_accum = 0.
        self.zoom_ratio = None
        self.zoom_accum = 0.

    def index_scroll(self, f, now, result):
        """Scroll by index pose, never by the hand's position in the image.

        Once unlocked, a clear pose emits one small step per fresh result.
        After missing/reacquired tracking the first pose is a quiet baseline.
        Never repeat or accumulate steps while waiting for inference.
        """
        direction = f.get("index_scroll_direction") if f.get("index_scroll_pose") else None
        if direction not in ("up", "down"):
            self.clear_motion()
            result["event"] = "INDEX_NEUTRAL" if f.get("index_scroll_pose") else "NEED_FOUR_FOLDED"
            return result
        self.scroll_direction = direction
        if self.scroll_baseline_needed:
            self.scroll_baseline_needed = False
            self.last_scroll_at = None
            result["event"] = "INDEX_UP_HOLD" if direction == "up" else "INDEX_DOWN_HOLD"
            return result
        self.scroll_direction_count += 1
        if self.last_scroll_at is not None and now - self.last_scroll_at < .3:
            result["event"] = "INDEX_HOLD"
            return result
        self.last_scroll_at = now
        result.update(event="CONTENT_UP" if direction == "up" else "CONTENT_DOWN",
                      scroll_px=16. if direction == "up" else -16.)
        return result

    def reset(self, reason="RESET"):
        auto_pointing_enabled = self.auto_pointing_enabled
        self.__init__()
        self.auto_pointing_enabled = auto_pointing_enabled
        return {"mode": "IDLE", "event": reason, "scroll_px": 0., "zoom_factor": 1.}

    def suspend(self, reason="WAITING_HAND"):
        """Pause motion, retaining explicit mode and the held-pinch latch.

        Missing/invalid landmarks are not a lock command. Re-entry starts
        with a fresh baseline and cannot replay motion across the gap.
        """
        self.clear_motion()
        self.candidate = None
        self.candidate_count = 0
        self.tracking_missing = True
        self.scroll_baseline_needed = True
        event = reason if self.mode != "IDLE" else "NO_HAND_RELEASE"
        return {"mode": self.mode, "event": event, "scroll_px": 0., "zoom_factor": 1.}

    def confirmed(self, name, now):
        if self.candidate != name:
            self.candidate, self.candidate_start, self.candidate_count = name, now, 1
        else:
            self.candidate_count += 1
        return self.candidate_count >= 2 and now - self.candidate_start >= .35

    def update(self, f, now, reacquired=False):
        if self.last_time is not None and now <= self.last_time:
            return self.reset("TIME_GAP_RELEASE")
        if f is None:
            return self.suspend()
        if self.last_time is not None and now - self.last_time > 6.:
            self.suspend("INPUT_STALE")
        self.last_time = now
        reacquired = reacquired or self.tracking_missing
        self.tracking_missing = False
        if reacquired:
            self.scroll_baseline_needed = True
            # A newly detected ROI is not continuous motion evidence. Keep
            # the held-pinch latch, but restart movement and confirmations.
            self.clear_motion()
            self.candidate = None
        result = {"mode": self.mode, "event": "WAIT", "scroll_px": 0., "zoom_factor": 1.}
        ratio = f["pinch_ratio"]
        # A bent index close to a tucked thumb resembles a pinch numerically.
        # A recognized four-finger fist has priority over pinch toggling.
        if f.get("index_scroll_pose"):
            if self.mode == "SCROLL":
                self.candidate = None
                if ratio > .45:
                    # A straight index separated from the tucked thumb is a
                    # completed pinch release, so a later new pinch can lock.
                    self.pinch_down = self.pinch_latched = False
                return self.index_scroll(f, now, result)
            if self.mode == "IDLE" and not f.get("pointing"):
                self.clear_motion()
                self.candidate = None
                result["event"] = "NEED_INDEX_TO_START"
                return result
        elif self.mode == "SCROLL":
            self.clear_motion()
        # A single noisy open observation must not re-arm a held pinch.
        # Require the same temporal evidence as activation/mode changes and
        # suppress motion until the release is confirmed.
        if ratio > .45 and self.pinch_down:
            self.clear_motion()
            result["event"] = "PINCH_RELEASE_HOLD"
            if self.confirmed("pinch_release", now):
                self.pinch_down = False
                self.pinch_latched = False
                self.candidate = None
                result["event"] = "PINCH_RELEASED"
                if self.mode == "SCROLL" and f.get("pointing"):
                    self.scroll_y = f["index_y"]
            return result
        if self.candidate == "pinch_release":
            self.candidate = None
        if ratio < .30:
            self.pinch_down = True
        elif ratio > .45:
            self.pinch_down = False
            self.pinch_latched = False

        if self.pinch_down:
            # Pinching only unlocks/locks. Position changes do not scroll.
            if self.mode == "SCROLL" and self.pinch_latched:
                self.clear_motion()
                result["event"] = "PINCH_WAIT_INDEX"
                return result
            self.clear_motion()
            result["event"] = "PINCH_HOLD"
            if not self.pinch_latched and self.confirmed("pinch", now):
                self.mode = "SCROLL" if self.mode == "IDLE" else "IDLE"
                self.auto_pointing_enabled = self.mode == "SCROLL"
                self.pinch_latched = True
                result["event"] = "ACTIVATE" if self.mode == "SCROLL" else "DEACTIVATE"
                self.candidate = None
            result["mode"] = self.mode
            return result

        if self.mode == "IDLE":
            # A deliberate index-only pose can start/resume scrolling after
            # tracking loss. An explicit pinch-to-stop remains latched off.
            if self.auto_pointing_enabled and ratio > .45 and f.get("pointing"):
                result["event"] = "POINT_HOLD"
                if self.confirmed("point_activate", now):
                    self.mode = "SCROLL"
                    self.clear_motion()
                    self.scroll_y = f["index_y"]
                    self.candidate = None
                    result.update(mode=self.mode, event="ACTIVATE")
            else:
                self.candidate = None
            return result

        switch = ("zoom" if self.mode == "SCROLL" and f["open_palm"] else
                  "scroll" if self.mode == "ZOOM" and f["pointing"] else None)
        if switch:
            self.clear_motion()
            result["event"] = "MODE_HOLD"
            if self.confirmed(switch, now):
                self.mode = switch.upper()
                self.candidate = None
                if self.mode == "SCROLL":
                    self.scroll_y = f["index_y"]
                else:
                    self.zoom_ratio = ratio
                result.update(mode=self.mode, event="ENTER_" + self.mode)
            return result
        self.candidate = None
        if self.mode == "SCROLL":
            return self.index_scroll(f, now, result)
        else:
            if self.zoom_ratio is None:
                self.zoom_ratio = ratio
                result["event"] = "ZOOM_BASELINE"
                return result
            # Compare consecutive observed distances. Smoothing the baseline
            # kept moving the old way after the user's fingers reversed.
            self.zoom_accum += math.log(ratio / self.zoom_ratio)
            self.zoom_ratio = ratio
            if abs(self.zoom_accum) >= .03:
                delta = math.copysign(math.floor(abs(self.zoom_accum) / .03) * .03,
                                      self.zoom_accum)
                bounded = float(np.clip(delta, math.log(.9), math.log(1.1)))
                self.zoom_accum = 0. if bounded != delta else self.zoom_accum - delta
                result.update(event="ZOOM_IN" if bounded > 0 else "ZOOM_OUT",
                              zoom_factor=math.exp(bounded))
        return result
