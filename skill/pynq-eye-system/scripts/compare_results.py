"""Compare numerical outputs under the same input contract; no model execution.

Input JSON: {context: {input_sha256, layout, dtype}, outputs: {name: array}}.
Optional metadata records backend and origin without changing the comparison.
Every numeric leaf must satisfy abs(actual-reference) <= atol + rtol*abs(reference).
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re


def validate(document):
    if not isinstance(document, dict) or not isinstance(document.get('context'), dict):
        raise ValueError('context object required')
    ctx = document['context']
    if not re.fullmatch(r'[a-fA-F0-9]{64}', str(ctx.get('input_sha256', ''))):
        raise ValueError('input_sha256 must identify the tested input')
    if not all(isinstance(ctx.get(k), str) and ctx[k] for k in ('layout', 'dtype')):
        raise ValueError('layout and dtype required')
    if not isinstance(document.get('outputs'), dict) or not document['outputs']:
        raise ValueError('Nonempty outputs object required')
    return ctx


def compare(reference, candidate, atol, rtol):
    if not all(math.isfinite(x) and x >= 0 for x in (atol, rtol)):
        raise ValueError('Tolerances must be finite and nonnegative')
    a, b = validate(reference), validate(candidate)
    if a != b:
        raise ValueError('Input contracts differ; do not compare different samples or preprocessing')
    if set(reference['outputs']) != set(candidate['outputs']):
        raise ValueError('Output names differ')
    result = dict(passed=True, compared_values=0, failed_values=0, max_abs_error=0.0,
                  atol=atol, rtol=rtol, first_failures=[])

    def visit(x, y, path):
        if isinstance(x, list) or isinstance(y, list):
            if not isinstance(x, list) or not isinstance(y, list) or len(x) != len(y) or not x:
                raise ValueError('Shape mismatch or empty output: ' + path)
            for i, (u, v) in enumerate(zip(x, y)):
                visit(u, v, path + '/' + str(i))
            return
        if type(x) not in (int, float) or type(y) not in (int, float):
            raise ValueError('Numeric leaves required: ' + path)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('Nonfinite output: ' + path)
        error = abs(y-x)
        result['compared_values'] += 1
        result['max_abs_error'] = max(result['max_abs_error'], error)
        if error > atol + rtol * abs(x):
            result['failed_values'] += 1
            if len(result['first_failures']) < 10:
                result['first_failures'].append(dict(path=path, reference=x, actual=y, abs_error=error))

    for key in reference['outputs']:
        visit(reference['outputs'][key], candidate['outputs'][key], key)
    result['passed'] = result['failed_values'] == 0
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--atol', type=float, required=True)
    p.add_argument('--rtol', type=float, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    try:
        result = compare(json.loads(a.reference.read_text(encoding='utf-8-sig')),
                         json.loads(a.candidate.read_text(encoding='utf-8-sig')), a.atol, a.rtol)
        result['source_sha256'] = {name:hashlib.sha256(path.read_bytes()).hexdigest()
                                  for name, path in [('reference', a.reference), ('candidate', a.candidate)]}
        code = 0 if result['passed'] else 1
    except (OSError, ValueError, TypeError) as exc:
        result, code = dict(passed=False, error=str(exc)), 2
    if a.output:
        with a.output.open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
