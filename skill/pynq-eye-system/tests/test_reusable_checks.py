"""Only new reusable validators: synthetic fixtures, no existing board retest."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_results import compare
from check_implementation import check

REF = dict(context=dict(input_sha256='0'*64, layout='NCHW', dtype='float32'), outputs={'logits':[[0.,1.,-2.]]})
UTIL = '''| Device : xc7z020clg400-1 |
| Design State : Routed |
| Slice LUTs | 100 | 0 | 0 | 53200 | 0.19 |
| Slice Registers | 200 | 0 | 0 | 106400 | 0.19 |
| Block RAM Tile | 2.5 | 0 | 0 | 140 | 1.79 |
| DSPs | 4 | 0 | 0 | 220 | 1.82 |'''
TIMING = '''WNS(ns) TNS(ns) TNS Failing Endpoints TNS Total Endpoints WHS(ns) THS(ns) THS Failing Endpoints THS Total Endpoints WPWS(ns) TPWS(ns) TPWS Failing Endpoints TPWS Total Endpoints
------- ------- -------
0.10 0.0 0 100 0.05 0.0 0 100 0.2 0.0 0 100'''
DRC = '''Violations found: 2
| DPIP-1 | Warning | Input pipelining | 2 |'''
LIMITS = dict(device='xc7z020clg400-1', max_resources=dict(lut=200,ff=300,bram=5,dsp=10))


class NumericalTests(unittest.TestCase):
    def test_reference_scaled_tolerance(self):
        candidate=copy.deepcopy(REF);candidate['outputs']['logits'][0][2]=-2.001
        self.assertTrue(compare(REF,candidate,0.,.001)['passed'])
        self.assertFalse(compare(REF,candidate,0.,0.)['passed'])

    def test_shape_and_empty_outputs_rejected(self):
        for output in ([],[[0.,1.]],'wrong'):
            c=copy.deepcopy(REF);c['outputs']['logits']=output
            with self.assertRaises(ValueError):compare(REF,c,0.,0.)

    def test_different_input_and_layout_rejected(self):
        for key,value in [('input_sha256','1'*64),('layout','NHWC')]:
            c=copy.deepcopy(REF);c['context'][key]=value
            with self.assertRaises(ValueError):compare(REF,c,.1,.1)

    def test_nonfinite_boolean_and_negative_tolerance_rejected(self):
        for value in [float('nan'),float('inf'),True]:
            c=copy.deepcopy(REF);c['outputs']['logits'][0][0]=value
            with self.assertRaises(ValueError):compare(REF,c,0.,0.)
        with self.assertRaises(ValueError):compare(REF,REF,-1.,0.)


class ImplementationTests(unittest.TestCase):
    def test_warning_retained_without_fabricating_clean_drc(self):
        result=check(UTIL,TIMING,DRC,LIMITS)
        self.assertTrue(result['passed']);self.assertEqual(result['drc'][0]['count'],2)

    def test_negative_slack_and_critical_drc_fail(self):
        self.assertFalse(check(UTIL,TIMING.replace('0.10','-0.10'),DRC,LIMITS)['passed'])
        self.assertFalse(check(UTIL,TIMING,DRC.replace('| Warning |','| Critical Warning |'),LIMITS)['passed'])

    def test_budget_and_device_fail(self):
        limits=copy.deepcopy(LIMITS);limits['max_resources']['lut']=99
        self.assertFalse(check(UTIL,TIMING,DRC,limits)['passed'])
        limits['device']='other'
        self.assertIn('device differs',check(UTIL,TIMING,DRC,limits)['failures'])

    def test_missing_or_partial_reports_fail_closed(self):
        for util,time,drc in [(UTIL.replace('Block RAM Tile','Unknown'),TIMING,DRC),
                              (UTIL,'',DRC),(UTIL,TIMING,DRC.replace('found: 2','found: 3'))]:
            with self.assertRaises(ValueError):check(util,time,drc,LIMITS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
