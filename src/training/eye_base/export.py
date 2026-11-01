"""Export trained checkpoints to fixed-shape ONNX for OpenCV/PYNQ CPU inference."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import torch
from torch import nn
from .models import build_classifier, MobileUNet

ROOT=Path(__file__).resolve().parent


class _OpenCVHardsigmoid(nn.Module):
    """Hardsigmoid written with ONNX primitives supported by OpenCV 4.5."""
    def forward(self, x):
        return torch.clamp(x + 3.0, min=0.0, max=6.0) / 6.0


class _OpenCVHardswish(nn.Module):
    """Hardswish written without exporting a HardSigmoid operator."""
    def forward(self, x):
        return x * (torch.clamp(x + 3.0, min=0.0, max=6.0) / 6.0)


def replace_opencv_unsupported_activations(module):
    """Replace torchvision activations that OpenCV 4.5.x cannot import."""
    for name, child in list(module.named_children()):
        if isinstance(child, nn.Hardsigmoid):
            setattr(module, name, _OpenCVHardsigmoid())
        elif isinstance(child, nn.Hardswish):
            setattr(module, name, _OpenCVHardswish())
        else:
            replace_opencv_unsupported_activations(child)
    return module

def export_one(checkpoint, output, task):
    state=torch.load(checkpoint,map_location='cpu',weights_only=False)
    model=build_classifier(False) if task=='classifier' else MobileUNet()
    model.load_state_dict(state['state_dict']); model.eval()
    if task == 'classifier':
        replace_opencv_unsupported_activations(model)
    size=160 if task=='classifier' else 128
    output.parent.mkdir(parents=True,exist_ok=True)
    torch.onnx.export(model,torch.zeros(1,3,size,size),str(output),opset_version=11,
                      input_names=['image'],output_names=['logits'],dynamic_axes=None,
                      do_constant_folding=True)
    meta=dict(task=task,architecture=state['architecture'],classes=state['classes'],image_size=size,
              output_activation=state.get('output_activation','sigmoid'),thresholds=state.get('thresholds'),
              normalization=state['normalization'],supervision=state['supervision'],onnx_opset=11)
    output.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'onnx':str(output),'metadata':str(output.with_suffix('.json')),'meta':meta},ensure_ascii=False,indent=2))

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('--classifier',type=Path,default=ROOT/'runs/classifier/best.pt'); p.add_argument('--localizer',type=Path,default=ROOT/'runs/localizer/best.pt'); p.add_argument('--output',type=Path,default=ROOT/'artifacts'); a=p.parse_args(argv)
    export_one(a.classifier,a.output/'mobilenet_v3_small.onnx','classifier')
    export_one(a.localizer,a.output/'mobile_unet.onnx','localizer')
    cls_state=torch.load(a.classifier,map_location='cpu',weights_only=False)
    (a.output/'model_config.json').write_text(json.dumps({'classification_thresholds':cls_state.get('thresholds',[.5,.5,.5]),'input_size':160,'class_names':cls_state['classes']},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
