"""Read Vivado routed reports against explicit project limits; never run Vivado.

Supported: Vivado text utilization, timing summary and DRC summary tables.
Unknown/incomplete formats fail closed. Passing does not sign off CDC, board I/O,
unconstrained paths, physical wiring, or a newly rebuilt bitstream.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re


def number(value):
    value = float(value.replace(',', ''))
    if not math.isfinite(value):
        raise ValueError('Nonfinite report value')
    return value


def parse_utilization(text):
    device = re.search(r'\|\s*Device\s*:\s*([^|\s]+)', text)
    state = re.search(r'\|\s*Design State\s*:\s*([^|\r\n]+)', text)
    if not device or not state or 'routed' not in state[1].lower():
        raise ValueError('Routed report and device required')
    labels = {'Slice LUTs':'lut', 'Slice Registers':'ff', 'Block RAM Tile':'bram', 'DSPs':'dsp'}
    usage = {}
    for line in text.splitlines():
        cells = [x.strip() for x in line.strip().strip('|').split('|')]
        label = cells[0].rstrip('*').strip()
        if line.lstrip().startswith('|') and label in labels:
            usage[labels[label]] = number(cells[1])
    if set(usage) != set(labels.values()):
        raise ValueError('Missing utilization table fields')
    return device[1], usage


def parse_timing(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith('WNS(ns)') and 'WHS(ns)' in line:
            for row in lines[i+1:i+6]:
                fields = row.split()
                if len(fields) == 12:
                    try:
                        values = [number(x) for x in fields]
                    except ValueError:
                        continue
                    return dict(wns_ns=values[0], tns_ns=values[1], setup_failing=values[2],
                                whs_ns=values[4], ths_ns=values[5], hold_failing=values[6],
                                wpws_ns=values[8], tpws_ns=values[9], pulse_failing=values[10])
    raise ValueError('Overall timing summary absent or unsupported')


def parse_drc(text):
    match = re.search(r'Violations found:\s*(\d+)', text)
    if not match:
        raise ValueError('DRC violation count absent')
    rows = []
    for line in text.splitlines():
        fields = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(fields) == 4 and re.fullmatch(r'[A-Z0-9_]+-\d+', fields[0]):
            if fields[1] not in ('Error', 'Critical Warning', 'Warning', 'Advisory'):
                raise ValueError('Unknown DRC severity')
            rows.append(dict(rule=fields[0], severity=fields[1], count=int(fields[3])))
    if sum(r['count'] for r in rows) != int(match[1]):
        raise ValueError('DRC table is incomplete')
    return rows


def check(utilization, timing, drc, limits):
    device, usage = parse_utilization(utilization)
    values, rules = parse_timing(timing), parse_drc(drc)
    problems = []
    if device != limits['device']:
        problems.append('device differs')
    for key in ('lut','ff','bram','dsp'):
        maximum = limits['max_resources'][key]
        if type(maximum) not in (int,float) or not math.isfinite(maximum) or maximum < 0:
            raise ValueError('Invalid resource limit')
        if usage[key] > maximum:
            problems.append(key+' exceeds limit')
    if any(values[k] < 0 for k in ('wns_ns','tns_ns','whs_ns','ths_ns','wpws_ns','tpws_ns')):
        problems.append('negative slack')
    if any(values[k] != 0 for k in ('setup_failing','hold_failing','pulse_failing')):
        problems.append('timing failing endpoints')
    if any(r['severity'] in ('Error','Critical Warning') and r['count'] for r in rules):
        problems.append('DRC error or critical warning')
    return dict(passed=not problems, device=device, usage=usage, timing=values,
                drc=rules, failures=problems,
                scope='Resource/timing/DRC gate only; warnings, CDC and unconstrained paths require separate review')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('utilization','timing','drc','limits'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    try:
        result = check(a.utilization.read_text(encoding='utf-8-sig'), a.timing.read_text(encoding='utf-8-sig'),
                       a.drc.read_text(encoding='utf-8-sig'), json.loads(a.limits.read_text(encoding='utf-8-sig')))
        result['source_sha256'] = {n:hashlib.sha256(getattr(a,n).read_bytes()).hexdigest()
                                  for n in ('utilization','timing','drc','limits')}
        code = 0 if result['passed'] else 1
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        result, code = dict(passed=False, error=str(exc)), 2
    if a.output:
        with a.output.open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
