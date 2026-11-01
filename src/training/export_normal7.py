import json
import hashlib
import shutil
import os
from pathlib import Path
import numpy as np
import torch
import cv2
from torch import nn
from torchvision.models import mobilenet_v3_small
from export_all6_finetuned import replace
from train_normal7 import OUT, CLASSES, ZH
SOURCE=OUT/os.environ.get('NORMAL7_VARIANT','')

torch.set_num_threads(4)
ckpt=torch.load(SOURCE/'best_normal7.pt',map_location='cpu',weights_only=False)
model=mobilenet_v3_small(weights=None); model.classifier[3]=nn.Linear(1024,7)
model.load_state_dict(ckpt['state_dict']); model.eval(); replace(model)
stage=OUT/'stage'
stage.mkdir(parents=True, exist_ok=False)
onnx=stage/'mobilenet_v3_small.onnx'
torch.onnx.export(model,torch.zeros(1,3,160,160),str(onnx),opset_version=11,dynamo=False,input_names=['input'],output_names=['logits'])
net=cv2.dnn.readNetFromONNX(str(onnx))
data=np.load(OUT/'dataset7.npz'); ids=np.where(data['splits']=='val')[0]
normal_ids=ids[data['labels'][ids,6]>.5]; disease_ids=ids[data['labels'][ids,6]<.5]
chosen=list(disease_ids[:3])+list(normal_ids[:3]); errors=[]
for i in chosen:
    rgb=data['images_cls'][i].astype(np.float32)/255
    blob=np.ascontiguousarray(((rgb-[.485,.456,.406])/[.229,.224,.225]).transpose(2,0,1)[None],dtype=np.float32)
    with torch.no_grad(): expected=model(torch.from_numpy(blob)).numpy()
    net.setInput(blob); actual=net.forward(); errors.append(float(np.abs(actual-expected).max()))
assert max(errors)<1e-3,errors
config=json.loads((Path(__file__).resolve().parents[2]/'data/models/eye/model_config.json').read_text(encoding='utf-8'))
config.update(classes=CLASSES,class_names_zh=ZH,classification_thresholds=[round(x,6) for x in ckpt['thresholds']],
              trained=__import__('datetime').date.today().isoformat(),source_checkpoint=str(SOURCE/'best_normal7.pt'),
              decision_rule='disease priority; normal only when its threshold is met and all six disease scores are below theirs; otherwise uncertain',
              note='research prototype with learned normal category; not for diagnosis')
(stage/'model_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
evidence=dict(onnx_sha256=hashlib.sha256(onnx.read_bytes()).hexdigest(),max_logit_error=max(errors),cases=len(chosen),thresholds=config['classification_thresholds'])
(OUT/'export_validation.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print(json.dumps(evidence))
