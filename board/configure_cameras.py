"""Write explicit camera roles for a newly installed system; never open cameras."""
import argparse
import json
from pathlib import Path


def configure(template, output, eye, gesture, confirmed=False, check_devices=False):
    if not confirmed:
        raise ValueError('Confirm physical eye and gesture roles with --confirm-roles')
    if output.exists():
        raise ValueError('Output exists; preserve it and choose a new output')
    if eye == gesture or not all(p.startswith('/dev/v4l/by-path/') for p in (eye, gesture)):
        raise ValueError('Use distinct /dev/v4l/by-path/ camera paths')
    if check_devices:
        a, b = Path(eye).resolve(strict=True), Path(gesture).resolve(strict=True)
        if a == b or not a.is_char_device() or not b.is_char_device():
            raise ValueError('Camera paths must resolve to distinct character devices')
    config = json.loads(template.read_text(encoding='utf-8'))
    config.update(eye=eye, gesture=gesture, roles_confirmed=True,
                  note='Physical roles explicitly selected for this installation.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--eye', required=True)
    p.add_argument('--gesture', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--confirm-roles', action='store_true')
    p.add_argument('--check-devices', action='store_true')
    a = p.parse_args()
    configure(Path(__file__).parent/'config/camera_roles.example.json', a.output,
              a.eye, a.gesture, a.confirm_roles, a.check_devices)
