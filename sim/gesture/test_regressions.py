import json
from pathlib import Path
import unittest
import numpy as np
from gesture_control import features, GestureControl
from test_index_bend import points, pose


class RegressionTests(unittest.TestCase):
    def test_thumb_resting_lower_on_fist_is_accepted_and_json_serializable(self):
        for direction in ('up', 'down'):
            p = points(direction)
            p[4, :2] = [110, 200]
            for degrees in (-90, 0, 45, 180):
                q = p.copy(); a = np.deg2rad(degrees)
                q[:, :2] = q[:, :2] @ np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]) * 1.3 + [80, 40]
                f = features(q)
                self.assertTrue(f['thumb_folded'])
                self.assertEqual(f['index_scroll_direction'], direction)
                json.dumps(f)

    def test_thumb_outside_palm_and_open_hand_do_not_scroll(self):
        for xy in ([0, 100], [0, 180], [220, 200], [100, 280]):
            p = points(); p[4, :2] = xy
            self.assertFalse(features(p)['index_scroll_pose'], xy)
        p = points()
        for base in (9, 13, 17):
            p[base+3, :2] = p[base, :2] + [0, -90]
        self.assertFalse(features(p)['index_scroll_pose'])

    def test_dropped_observation_retains_unlock_but_returns_quietly(self):
        c = GestureControl(); c.mode = 'SCROLL'
        self.assertEqual(c.update(pose(), 1.)['event'], 'CONTENT_UP')
        for t in (2., 10., 300.):
            self.assertEqual(c.update(None, t)['scroll_px'], 0.)
            self.assertEqual(c.mode, 'SCROLL')
        self.assertEqual(c.update(pose('down'), 301.)['scroll_px'], 0.)
        self.assertEqual(c.update(pose('down'), 303.)['scroll_px'], -16.)

    def test_recorded_clear_poses_emit_without_second_confirmation(self):
        rows = json.loads((Path(__file__).resolve().parents[2] / 'data/gesture/recorded_results.json').read_text(encoding='utf-8'))
        samples = [r for r in rows if r['event'] in ('INDEX_UP_HOLD', 'INDEX_DOWN_HOLD')]
        self.assertGreaterEqual(len(samples), 10)
        for r in samples:
            c = GestureControl(); c.mode = 'SCROLL'
            result = c.update(r['features'], r['at'])
            self.assertEqual(result['event'], 'CONTENT_' + r['features']['index_scroll_direction'].upper())
            self.assertEqual(abs(result['scroll_px']), 16.)

if __name__ == '__main__':
    unittest.main()
