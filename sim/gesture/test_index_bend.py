import unittest
import numpy as np
from gesture_control import GestureControl,features

def points(index='up'):
    p=np.zeros((21,3),float);p[0,:2]=[100,220]
    p[1:5,:2]=[[70,200],[50,170],[80,155],[95,150]]
    for base,x,y in [(5,65,140),(9,90,130),(13,115,135),(17,140,150)]:
        p[base:base+4,:2]=[[x,y],[x,y-30],[x+5,y-10],[x+5,y+5]]
    if index=='up':p[5:9,:2]=[[65,140],[65,110],[65,80],[65,50]]
    elif index=='down':p[8,:2]=[85,150]
    else:p[5:9,:2]=[[65,140],[65,110],[75,95],[84,87]]
    return p

def pose(direction='up',y=200,eligible=True,ratio=1.):
    return dict(index_y=y,pinch_ratio=ratio,pointing=direction=='up' and eligible,
                open_palm=False,index_scroll_pose=eligible,
                index_scroll_direction=direction if eligible else None)

class IndexBendTests(unittest.TestCase):
    def quiet(self,r,mode='SCROLL'):
        self.assertEqual(r['mode'],mode)
        self.assertEqual((r['scroll_px'],r['zoom_factor']),(0.,1.))
    def active(self):
        c=GestureControl();c.update(pose(),1.)
        self.assertEqual(c.update(pose(),2.)['event'],'ACTIVATE')
        return c
    def test_synthetic_landmarks_up_down_and_neutral(self):
        for state in ['up','down',None]:
            f=features(points(state))
            self.assertTrue(f['index_scroll_pose'])
            self.assertEqual(f['index_scroll_direction'],state)
    def test_thumb_out_or_another_finger_extended_disables_pose(self):
        p=points();p[4,:2]=[0,100]
        self.assertFalse(features(p)['index_scroll_pose'])
        p=points();p[9:13,:2]=[[90,130],[90,100],[90,70],[90,40]]
        self.assertFalse(features(p)['index_scroll_pose'])
    def test_translation_rotation_and_scale_do_not_change_direction(self):
        for kind in ['up','down',None]:
            for degrees in [-90,-30,45,180]:
                p=points(kind);a=np.deg2rad(degrees)
                rot=np.array([[np.cos(a),-np.sin(a)],[np.sin(a),np.cos(a)]])
                p[:,:2]=p[:,:2]@rot*1.3+[20,30]
                self.assertEqual(features(p)['index_scroll_direction'],kind)
    def test_up_uses_pose_instead_of_vertical_movement(self):
        c=self.active();self.assertEqual(c.update(pose('up',100),3.)['event'],'CONTENT_UP')
        r=c.update(pose('up',400),4.)
        self.assertEqual((r['event'],r['scroll_px']),('CONTENT_UP',16.))
        self.assertEqual(c.update(pose('up',400),5.)['event'],'CONTENT_UP')
    def test_bent_index_scrolls_down_without_pinch_toggle(self):
        c=self.active();f=features(points('down'))
        self.assertLess(f['pinch_ratio'],.3)
        self.assertEqual(c.update(f,3.)['event'],'CONTENT_DOWN')
        r=c.update(f,4.)
        self.assertEqual((r['mode'],r['event'],r['scroll_px']),('SCROLL','CONTENT_DOWN',-16.))
    def test_clear_reversal_changes_direction_without_extra_inference_delay(self):
        c=self.active();c.update(pose('up'),3.);c.update(pose('up'),4.)
        self.assertEqual(c.update(pose('down'),5.)['event'],'CONTENT_DOWN')
        self.assertEqual(c.update(pose('down'),6.)['event'],'CONTENT_DOWN')
    def test_mid_pose_and_other_fingers_stop_immediately(self):
        for f in [pose(None),pose('up',eligible=False)]:
            c=self.active();c.update(pose(),3.);c.update(pose(),4.)
            self.quiet(c.update(f,5.));self.assertEqual(c.update(pose(),6.)['event'],'CONTENT_UP')
    def test_hand_absence_retains_mode_and_reconfirms(self):
        for direction in ['up','down']:
            c=self.active();c.update(pose(direction),3.);c.update(pose(direction),4.)
            self.quiet(c.update(None,300.));self.quiet(c.update(pose(direction),301.))
            self.assertEqual(c.update(pose(direction),302.)['event'],'CONTENT_'+direction.upper())
    def test_long_gap_and_reacquisition_do_not_emit_immediately(self):
        for gap in [False,True]:
            c=self.active();c.update(pose(),3.);c.update(pose(),4.)
            self.quiet(c.update(pose(),30. if gap else 5.,reacquired=not gap))
    def test_closed_fist_cannot_unlock_idle(self):
        c=GestureControl()
        for t in [1.,2.,3.]:self.quiet(c.update(features(points('down')),t),'IDLE')
    def test_pinch_unlock_and_explicit_lock_still_work(self):
        c=GestureControl();pinch=pose(None,eligible=False,ratio=.2)
        c.update(pinch,1.);self.assertEqual(c.update(pinch,2.)['event'],'ACTIVATE')
        self.assertEqual(c.update(pose(),3.)['event'],'CONTENT_UP');c.update(pose(),4.)
        self.assertFalse(c.pinch_latched)
        self.quiet(c.update(pinch,5.));self.assertEqual(c.update(pinch,6.)['event'],'DEACTIVATE')
        for t in [7.,8.,9.,10.]:self.quiet(c.update(pose(),t),'IDLE')
    def test_pinch_height_motion_no_longer_scrolls(self):
        c=GestureControl();pinch=pose(None,eligible=False,ratio=.2)
        c.update(pinch,1.);c.update(pinch,2.)
        pinch['index_y']=450;self.quiet(c.update(pinch,3.))
        pinch['index_y']=50;self.quiet(c.update(pinch,4.))
    def test_fast_results_are_rate_limited_without_catchup(self):
        c=self.active();c.update(pose(),3.);c.update(pose(),4.)
        self.quiet(c.update(pose(),4.1));self.quiet(c.update(pose(),4.2))
        self.assertEqual(c.update(pose(),4.4)['scroll_px'],16.)
    def test_zoom_mode_still_works(self):
        c=self.active();f=dict(pose(eligible=False),open_palm=True)
        c.update(f,3.);self.assertEqual(c.update(f,4.)['event'],'ENTER_ZOOM')
        f['pinch_ratio']=1.2;self.assertEqual(c.update(f,5.)['event'],'ZOOM_IN')
    def test_timestamp_reset_and_stop(self):
        c=self.active();self.quiet(c.update(pose(),1.),'IDLE')
        c=self.active();self.quiet(c.reset(),'IDLE')

if __name__=='__main__':unittest.main()
