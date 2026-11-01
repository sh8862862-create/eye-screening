"""Export the all-six fine-tuned checkpoint for the board OpenCV runtime."""
import json
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision.models import mobilenet_v3_small

ROOT = Path(__file__).parent
CKPT = ROOT / "runs_all6_finetune" / "best_all6_finetuned.pt"
OUT = ROOT / "runs_all6_finetune" / "mobilenet_v3_small_all6_finetuned.onnx"
CONFIG = ROOT / "runs_all6_finetune" / "model_config_all6_finetuned.json"
CLASSES = ["pinguecula", "pterygium", "subconjunctival_hemorrhage",
           "hordeolum", "blepharitis", "dacryocystitis"]
ZH = ["睑裂斑", "翼状胬肉", "结膜下出血", "麦粒肿", "睑缘炎", "泪囊炎"]
THRESH = [.80, .55, .55, .50, .50, .50]


class HardswishSafe(nn.Module):
    def forward(self, x):
        return x * (torch.clamp(x + 3.0, 0.0, 6.0) / 6.0)


class HardsigmoidSafe(nn.Module):
    def forward(self, x):
        return torch.clamp(x + 3.0, 0.0, 6.0) / 6.0


def replace(module):
    for name, child in list(module.named_children()):
        if isinstance(child, nn.Hardswish):
            setattr(module, name, HardswishSafe())
        elif isinstance(child, nn.Hardsigmoid):
            setattr(module, name, HardsigmoidSafe())
        else:
            replace(child)


def build(safe=False):
    checkpoint = torch.load(CKPT, map_location="cpu", weights_only=False)
    model = mobilenet_v3_small(weights=None)
    model.classifier[3] = nn.Linear(model.classifier[3].in_features, len(CLASSES))
    if safe:
        replace(model)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model


def main():
    model = build(safe=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.zeros(1, 3, 160, 160), OUT,
                      opset_version=11, dynamo=False,
                      input_names=["input"], output_names=["logits"])
    # Compare safe activation graph with the original PyTorch graph.
    reference = build(safe=False)
    x = torch.randn(2, 3, 160, 160)
    with torch.no_grad():
        a = reference(x).numpy()
        b = model(x).numpy()
    diff = float(np.abs(a - b).max())
    net = cv2.dnn.readNetFromONNX(str(OUT))
    net.setInput(x.numpy())
    onnx_logits = net.forward()
    onnx_diff = float(np.abs(onnx_logits - b).max())
    config = {
        "task": "classifier", "architecture": "mobilenet_v3_small",
        "classes": CLASSES, "class_names_zh": ZH,
        "classification_thresholds": THRESH,
        "input_size": 160, "input_layout": "NCHW", "color": "RGB",
        "normalization": {"mean": [.485, .456, .406], "std": [.229, .224, .225]},
        "output_activation": "sigmoid", "onnx_opset": 11,
        "opencv_runtime": "4.5.4 compatible",
        "trained": "2026-10-23",
        "source_checkpoint": str(CKPT),
        "note": "research candidate; trained on all six classes; not for diagnosis"
    }
    CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"onnx": str(OUT), "bytes": OUT.stat().st_size,
                      "safe_vs_torch_max_abs": diff,
                      "opencv_vs_torch_max_abs": onnx_diff,
                      "opencv": cv2.__version__}, ensure_ascii=False))


if __name__ == "__main__":
    main()
