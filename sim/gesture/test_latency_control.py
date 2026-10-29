import unittest
from gesture_control import GestureControl
from test_index_bend import pose

class LatencyTests(unittest.TestCase):
    def test_confirmation_requires_two_frames_and_minimum_time(self):
        c=GestureControl();pinch=pose(eligible=False,ratio=.2)
        self.assertEqual(c.update(pinch,1.)['mode'],'IDLE')
        self.assertEqual(c.update(pinch,1.2)['mode'],'IDLE')
        self.assertEqual(c.update(pinch,1.36)['event'],'ACTIVATE')
        self.assertEqual(c.update(pinch,1.8)['mode'],'SCROLL')
        self.assertNotEqual(c.update(pinch,2.2)['event'],'DEACTIVATE')

    def test_fresh_400ms_updates_are_not_alternately_discarded(self):
        c=GestureControl();c.mode='SCROLL'
        self.assertEqual(c.update(pose(),1.)['event'],'CONTENT_UP')
        self.assertEqual(c.update(pose(),1.4)['event'],'CONTENT_UP')
        self.assertEqual(c.update(pose(),1.5)['event'],'INDEX_HOLD')
        c.update(None,2.)
        self.assertEqual(c.update(pose('down'),12.)['scroll_px'],0.)
        self.assertEqual(c.update(pose('down'),12.4)['event'],'CONTENT_DOWN')

if __name__=='__main__':unittest.main()
