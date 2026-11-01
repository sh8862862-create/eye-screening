"""Portable seven-class training entry. Outputs never replace supplied artifacts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'src/training'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'train', 'export'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--variant', choices=['balanced', 'constrained'], default='balanced')
    p.add_argument('--use-saved-dataset', action='store_true')
    a = p.parse_args()
    out = a.output.resolve()
    env = dict(os.environ, NORMAL7_ROOT=str(out), PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=str(SOURCE))
    for key in ['NORMAL7_BALANCED', 'NORMAL7_CONSTRAINED', 'NORMAL7_VARIANT']:
        env.pop(key, None)
    if a.action == 'prepare':
        if out.exists():
            raise ValueError('Output exists; choose a new experiment directory')
        out.mkdir(parents=True)
        if a.use_saved_dataset:
            shutil.copyfile(ROOT/'data/archive/workspace/model_retrain/normal7/dataset7.npz', out/'dataset7.npz')
        else:
            subprocess.run([sys.executable, '-B', '-c', 'import train_normal7; train_normal7.build_data()'],
                           env=env, check=True)
        (out/'experiment.json').write_text(json.dumps(dict(variant=a.variant, saved_dataset=a.use_saved_dataset,
            source='src/training/train_normal7.py', retrained=False), indent=2), encoding='utf-8')
    elif a.action == 'train':
        if not (out/'dataset7.npz').is_file() or (out/a.variant).exists():
            raise ValueError('Prepare first; variant output must not exist')
        env['NORMAL7_' + a.variant.upper()] = '1'
        subprocess.run([sys.executable, '-B', str(SOURCE/'train_normal7.py')], env=env, check=True)
    else:
        if not (out/a.variant/'best_normal7.pt').is_file():
            raise ValueError('Selected trained checkpoint missing')
        env['NORMAL7_VARIANT'] = a.variant
        subprocess.run([sys.executable, '-B', str(SOURCE/'export_normal7.py')], env=env, check=True)
    print(json.dumps(dict(action=a.action, output=str(out), variant=a.variant), indent=2))


if __name__ == '__main__':
    main()
