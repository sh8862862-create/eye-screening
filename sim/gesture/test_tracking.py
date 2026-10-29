import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/ps/gesture'))
from hand_tracker import HandTracker

def hand():
    h=np.zeros(132);p=h[4:67].reshape(21,3)
    p[:,:2]=[300,240]
    for i,xy in {0:[300,280],5:[270,230],9:[295,220],13:[320,230],17:[340,245],1:[275,265],2:[265,250],4:[285,245],8:[290,225]}.items():p[i,:2]=xy
    h[131]=.99
    return h
def roi():
    h=hand();return np.r_[260,215,345,285,h[4:67].reshape(21,3)[[0,5,9,13,17,1,2],:2].ravel(),.9]
class Palm:
    def __init__(self):self.calls=0;self.count=1
    def infer(self,frame):
        self.calls+=1
        return np.array([roi()]*self.count)
class Pose:
    def __init__(self):self.value=hand()
    def infer(self,frame,roi):return None if self.value is None else self.value.copy()
class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.palm=Palm();self.pose=Pose();self.t=HandTracker(self.palm,self.pose,max_tracks=2)
        self.frame=np.zeros((480,640,3),np.uint8)
        self.assertIsNotNone(self.t.infer(self.frame,1)[0])
    def test_fist_detection_miss_does_not_discard_fresh_valid_pose(self):
        self.palm.count=0
        for i in range(1,15):
            h,source,_=self.t.infer(self.frame,1+i*.5)
            self.assertIsNotNone(h);self.assertEqual(source,'TRACK')
        self.assertGreater(self.palm.calls,2)
    def test_missing_pose_never_replays_old_hand(self):
        self.palm.count=0;self.pose.value=None
        h,_,_=self.t.infer(self.frame,1.5)
        self.assertIsNone(h);self.assertIsNone(self.t.roi)
    def test_low_confidence_is_rejected(self):
        self.pose.value[131]=.79;self.palm.count=0
        self.assertIsNone(self.t.infer(self.frame,1.5)[0])
    def test_multiple_hands_stop_periodic_tracking(self):
        self.palm.count=2
        self.t.infer(self.frame,1.5);self.t.infer(self.frame,2.)
        h,_,issue=self.t.infer(self.frame,2.5)
        self.assertIsNone(h);self.assertIn('Multiple',issue)
    def test_time_gap_requires_new_detection(self):
        self.palm.count=0
        self.assertIsNone(self.t.infer(self.frame,20)[0])
    def test_repeated_rotation_does_not_inflate_roi(self):
        initial=self.t.roi_corners.copy();center=np.array([300,250])
        for angle in [10,-10]*10:
            a=np.deg2rad(angle);rot=np.array([[np.cos(a),-np.sin(a)],[np.sin(a),np.cos(a)]])
            points=(self.t.points-center)@rot+center
            new,corners=self.t.moved_roi(points)
            self.t.roi,self.t.roi_corners,self.t.points=new,corners,points
        np.testing.assert_allclose(self.t.roi_corners,initial,atol=.001)
    def test_tighter_crop_requires_fresh_confident_pose(self):
        class CropPose:
            PALM_BOX_ENLARGE_FACTOR=4
            def infer(self,frame,roi):
                return hand() if self.PALM_BOX_ENLARGE_FACTOR<=3.5 else None
        pose=CropPose();t=HandTracker(self.palm,pose)
        result=t.infer(self.frame,1)[0]
        self.assertIsNotNone(result);self.assertEqual(t.crop_factor,3.5)
        self.assertEqual(pose.PALM_BOX_ENLARGE_FACTOR,4)
    def test_failed_crop_retry_never_returns_stale_hand(self):
        self.pose.PALM_BOX_ENLARGE_FACTOR=4
        t=HandTracker(self.palm,self.pose);t.infer(self.frame,1)
        self.pose.value=None;self.palm.count=0
        self.assertIsNone(t.infer(self.frame,1.5)[0])
        self.assertIsNone(t.roi)

if __name__=='__main__':unittest.main()
