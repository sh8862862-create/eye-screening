"""Validate fresh pose every frame; detector misses must not discard valid tracking."""
import numpy as np

from gesture_control import features


PALM_IDS = [0, 5, 9, 13, 17, 1, 2]


class HandTracker:
    def __init__(self, palm, pose, max_tracks=2, max_gap=4.):
        self.palm, self.pose = palm, pose
        self.max_tracks, self.max_gap = max_tracks, max_gap
        self.base_crop_factor = getattr(pose, "PALM_BOX_ENLARGE_FACTOR", None)
        self.reset()

    def reset(self):
        self.roi = self.points = self.timestamp = self.shape = None
        self.tracks = 0
        self.roi_corners = None
        self.crop_factor = self.base_crop_factor

    @staticmethod
    def checked(hand, shape):
        if hand is None:
            raise ValueError("Hand confidence too low: release")
        hand = np.asarray(hand)
        if hand.shape != (132,) or not np.isfinite(hand).all() or hand[131] < .8:
            raise ValueError("Invalid hand result: release")
        points = hand[4:67].reshape(21, 3)
        # Keep valid model landmarks for the board-side visualization.
        # GestureControl still validates features before any action.
        xy = points[:, :2]
        height, width = shape[:2]
        # Wrist estimates can lie just outside the image when entering from an
        # edge. Allow a small border, but keep the two control tips visible.
        tips = xy[[4, 8]]
        if ((xy < [-.1 * width, -.1 * height]).any()
                or (xy > [1.1 * width, 1.1 * height]).any()
                or (tips < 0).any() or (tips >= [width, height]).any()):
            raise ValueError("Hand crosses image edge: release")
        return xy[PALM_IDS].copy()

    def moved_roi(self, points):
        # Transform the detector's ROI along with the palm. Rebuilding it from
        # hand landmarks alone changes the crop even on a stationary image.
        old_center, new_center = self.points.mean(0), points.mean(0)
        old, new = self.points - old_center, points - new_center
        u, singular, vt = np.linalg.svd(old.T @ new)
        rotation = u @ vt
        if np.linalg.det(rotation) < 0:
            raise ValueError("Palm orientation changed abruptly: release")
        scale = float(singular.sum() / max(float((old * old).sum()), 1e-6))
        palm_width = float(np.linalg.norm(self.points[1] - self.points[4]))
        residual = float(np.sqrt(np.mean(np.sum((old @ rotation * scale - new) ** 2, axis=1))))
        if not .7 <= scale <= 1.4 or np.linalg.norm(new_center - old_center) > palm_width or residual > .25 * palm_width:
            raise ValueError("Palm moved too far or changed shape: release")
        transform = lambda xy: (xy - old_center) @ rotation * scale + new_center
        x1, y1, x2, y2 = self.roi[:4]
        corners = transform(self.roi_corners if self.roi_corners is not None else
                            np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]))
        landmarks = transform(self.roi[4:18].reshape(7, 2))
        return np.r_[corners.min(0), corners.max(0), landmarks.ravel(), self.roi[18]].astype(np.float32), corners

    def infer_pose(self, frame, roi):
        # A curled finger occupies less of the former open-hand crop. Retry
        # once with a tighter crop, requiring a NEW high-confidence result.
        original = getattr(self.pose, 'PALM_BOX_ENLARGE_FACTOR', None)
        try:
            if self.crop_factor is not None:
                self.pose.PALM_BOX_ENLARGE_FACTOR = self.crop_factor
            hand = self.pose.infer(frame, roi.copy())
            if (hand is None or float(hand[131]) < .8) and self.crop_factor is not None:
                tighter = max(self.base_crop_factor * .75, self.crop_factor * .875)
                if tighter < self.crop_factor:
                    self.pose.PALM_BOX_ENLARGE_FACTOR = tighter
                    recovered = self.pose.infer(frame, roi.copy())
                    self.checked(recovered, frame.shape)
                    self.crop_factor = tighter
                    return recovered
            return hand
        finally:
            if original is not None:
                self.pose.PALM_BOX_ENLARGE_FACTOR = original

    def infer(self, frame, captured_at):
        tracking = (self.roi is not None
                    and self.shape == frame.shape
                    and 0 < captured_at - self.timestamp <= self.max_gap)
        source = "TRACK" if tracking else "DETECT"
        try:
            if tracking:
                roi = self.roi.copy()
            else:
                self.reset()
                palms = self.palm.infer(frame)
                if len(palms) != 1:
                    return None, source, ("Multiple hands: release; show one hand only" if len(palms) > 1 else "No palm: release")
                roi = np.asarray(palms[0], dtype=np.float32).copy()
                if roi.shape != (19,) or not np.isfinite(roi).all():
                    raise ValueError("Invalid palm: release")
            # MPHandPose preprocess modifies the supplied bbox in place.
            hand = self.infer_pose(frame, roi)
            points = self.checked(hand, frame.shape)
            if tracking:
                next_roi, corners = self.moved_roi(points)
                if self.tracks >= self.max_tracks:
                    # Keep the freshly validated pose if a closed fist is missed.
                    # Multiple hands remain an explicit stop condition.
                    palms = self.palm.infer(frame)
                    if len(palms) > 1:
                        self.reset()
                        return None, "DETECT", "Multiple hands: release; show one hand only"
                    self.tracks = 0
            else:
                next_roi = roi
                x1, y1, x2, y2 = roi[:4]
                corners = np.array([[x1,y1],[x2,y1],[x2,y2],[x1,y2]])
            self.roi_corners = corners
            self.roi, self.points = next_roi, points
            self.timestamp, self.shape = captured_at, frame.shape
            self.tracks = self.tracks + 1 if tracking else 0
            return hand, source, ""
        except ValueError as exc:
            self.reset()
            if tracking:
                # Retry this exact image once with a fresh palm ROI. reset()
                # makes the nested call DETECT, so a failure cannot recurse.
                hand, _, retry_issue = self.infer(frame, captured_at)
                return hand, "REDETECT", (retry_issue if hand is None else
                                          "Reacquired after: " + str(exc))
            return None, source, str(exc)
