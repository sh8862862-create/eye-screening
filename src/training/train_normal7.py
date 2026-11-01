"""Extend the deployed six disease outputs with an explicitly learned normal.
Preserve disease splits and partial labels; group new near-duplicates by hash.
"""
import csv
import hashlib
import json
import random
import time
import os
from pathlib import Path
import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, WeightedRandomSampler
from torchvision.models import mobilenet_v3_small
from finetune_board_all6 import DS, predict, THRESH as OLD_THRESH

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parents[1]
OUT = Path(os.environ.get('NORMAL7_ROOT', PACKAGE / 'build/work/normal7'))
CONSTRAINED = os.environ.get('NORMAL7_CONSTRAINED') == '1'
BALANCED = os.environ.get('NORMAL7_BALANCED') == '1'
if CONSTRAINED:
    OUT = OUT / 'constrained'
elif BALANCED:
    OUT = OUT / 'balanced'
NORMAL = Path(os.environ.get('EYE_NORMAL_DATA', PACKAGE / 'data/training/normal_500'))
DATA = Path(os.environ.get('EYE_DISEASE_DATA', PACKAGE / 'data/training/dataset6.npz'))
BASE = Path(os.environ.get('EYE_BASE_CHECKPOINT', PACKAGE / 'data/archive/workspace/model_retrain/runs_all6_finetune/best_all6_finetuned.pt'))
CLASSES = ['pinguecula','pterygium','subconjunctival_hemorrhage','hordeolum','blepharitis','dacryocystitis','normal']
ZH = ['睑裂斑','翼状胬肉','结膜下出血','麦粒肿','睑缘炎','泪囊炎','正常']
SEED = 20261023

def save(name, data):
    (OUT/name).write_text(json.dumps(data, indent=2, ensure_ascii=False),encoding='utf-8')

def crop(image):
    h,w=image.shape[:2]; scale=160/min(h,w)
    image=cv2.resize(image,(round(w*scale),round(h*scale)),interpolation=cv2.INTER_LINEAR)
    h,w=image.shape[:2]
    return cv2.cvtColor(image[(h-160)//2:(h-160)//2+160,(w-160)//2:(w-160)//2+160],cv2.COLOR_BGR2RGB)

def phash(rgb):
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    low=cv2.dct(cv2.resize(gray,(32,32)).astype(np.float32))[:8,:8].reshape(-1)
    bits=low[1:]>np.median(low[1:])
    return int(''.join('1' if b else '0' for b in bits),2)

def build_data():
    d=np.load(DATA,allow_pickle=True)
    x0,y0,s0=d['images_cls'],d['labels'],d['splits']
    n=len(x0)
    assert n==1553 and (y0.sum(1)>0).all()
    old_hashes=[phash(x) for x in x0]
    sources=list(csv.DictReader((NORMAL/'sources.csv').open(encoding='utf-8-sig')))
    records=[]; xs=[]; excluded=[]; seen={}
    for row in sources:
        p=NORMAL/'images'/row['file']
        raw=p.read_bytes(); sha=hashlib.sha256(raw).hexdigest()
        # Manifest may describe original pre-JPEG source; retain both hashes.
        manifest_matches_file = sha == row['sha256']
        image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None: raise ValueError('Unreadable image '+str(p))
        rgb=crop(image); px=hashlib.sha256(rgb.tobytes()).hexdigest(); ph=phash(rgb)
        if px in seen:
            excluded.append(dict(file=row['file'],reason='duplicate_pixels',duplicate_of=seen[px])); continue
        cross=[i for i,h in enumerate(old_hashes) if (h^ph).bit_count()<=2]
        if cross:
            excluded.append(dict(file=row['file'],reason='possible_disease_image_overlap',disease_indices=cross)); continue
        seen[px]=row['file']; xs.append(rgb)
        records.append(dict(row,actual_file_sha256=sha,manifest_matches_file=manifest_matches_file,
                            pixel_sha256=px,phash=hex(ph),path=str(p)))
    parent=list(range(len(records)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]]; i=parent[i]
        return i
    for i,r in enumerate(records):
        for j in range(i):
            if (int(r['phash'],16)^int(records[j]['phash'],16)).bit_count()<=4:
                parent[root(i)]=root(j)
    groups={}
    for i in range(len(records)): groups.setdefault(root(i),[]).append(i)
    rng=np.random.default_rng(SEED); splits=np.full(len(records),'train',dtype='<U5')
    for source in sorted({r['source'] for r in records}):
        selected=[g for g in groups.values() if records[g[0]]['source']==source]
        rng.shuffle(selected); total=sum(map(len,selected)); used=0
        for g in selected:
            kind='test' if used<total*.2 else 'val' if used<total*.4 else 'train'
            splits[g]=kind; used+=len(g)
    for i,r in enumerate(records): r.update(split=str(splits[i]),group='normal_%d'%root(i))
    x=np.concatenate([x0,np.asarray(xs)],axis=0)
    y=np.zeros((len(x),7),np.float32); y[:n,:6]=y0; y[n:,6]=1
    mask=np.zeros_like(y); mask[:1414,:3]=1; mask[1414:n,3:6]=1; mask[:n,6]=1; mask[n:]=1
    split=np.concatenate([s0,splits])
    np.savez_compressed(OUT/'dataset7.npz',images_cls=x,labels=y,mask=mask,splits=split)
    audit=dict(input_normal=len(sources),usable_normal=len(xs),excluded=excluded,
        normal_split_counts={s:int((splits==s).sum()) for s in ['train','val','test']},
        combined_counts={s:int((split==s).sum()) for s in ['train','val','test']},
        normal_groups=len(groups),manifest_mismatches=sum(not r['manifest_matches_file'] for r in records),records=records,
        limitation='Image/perceptual grouping only; no patient identity or clinical label review.')
    save('data_audit.json',audit)
    print('DATA',json.dumps({k:v for k,v in audit.items() if k not in ('records','excluded')},ensure_ascii=False),flush=True)
    return x,y,mask,split

class ExtendedHead(nn.Module):
    def __init__(self,old):
        super().__init__(); self.disease=old; self.normal=nn.Linear(old.in_features,1)
        nn.init.zeros_(self.normal.weight); nn.init.constant_(self.normal.bias,-1.5)
    def forward(self,x): return torch.cat((self.disease(x),self.normal(x)),dim=1)

def model():
    m=mobilenet_v3_small(weights=None); m.classifier[3]=nn.Linear(1024,6)
    m.load_state_dict(torch.load(BASE,map_location='cpu',weights_only=False)['state_dict'])
    m.classifier[3]=ExtendedHead(m.classifier[3]); return m

def binary_stats(pred,true):
    pred=np.asarray(pred,bool); true=np.asarray(true,bool)
    tp=int((pred&true).sum()); fp=int((pred&~true).sum()); fn=int((~pred&true).sum()); tn=int((~pred&~true).sum())
    p=tp/max(tp+fp,1); r=tp/max(tp+fn,1)
    return dict(tp=tp,fp=fp,fn=fn,tn=tn,precision=p,recall=r,f1=2*p*r/max(p+r,1e-12))

def evaluate(p,y,mask,normal_threshold):
    disease=y[:,6]<.5
    old=disease&(mask[:,0]>.5); new=disease&(mask[:,3]>.5)
    from finetune_board_all6 import report
    dr={'old':report(p[old,:6],y[old,:6],[0,1,2]),'new':report(p[new,:6],y[new,:6],[3,4,5])}
    disease_positive=(p[:,:6]>=OLD_THRESH).any(1)
    normal=(p[:,6]>=normal_threshold)&~disease_positive
    dr['normal']=binary_stats(normal,y[:,6]>.5)
    dr['disease_called_normal']=int((normal&disease).sum()); dr['disease_count']=int(disease.sum())
    dr['normal_called_disease']=int((disease_positive&~disease).sum()); dr['normal_count']=int((~disease).sum())
    dr['uncertain_count']=int((~normal&~disease_positive).sum()); dr['normal_threshold']=float(normal_threshold)
    return dr

def choose_threshold(p,y):
    disease_positive=(p[:,:6]>=OLD_THRESH).any(1); best=None
    for t in np.arange(.5,1.,.01):
        normal=(p[:,6]>=t)&~disease_positive; st=binary_stats(normal,y[:,6]>.5)
        if st['fp']!=0: continue
        if best is None or st['f1']>best[0]: best=(st['f1'],float(t))
    return best if best else (0.,.99)

def main():
    OUT.mkdir(parents=True, exist_ok=True); random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED); torch.set_num_threads(8)
    if CONSTRAINED or BALANCED:
        d=np.load(OUT.parent/'dataset7.npz'); x,y,mask,split=[d[k] for k in ['images_cls','labels','mask','splits']]
    else:
        x,y,mask,split=build_data()
    ids={s:np.where(split==s)[0] for s in ['train','val','test']}
    loaders={s:DataLoader(DS(x[ix],y[ix],mask[ix],s=='train'),batch_size=32,shuffle=s=='train') for s,ix in ids.items()}
    if BALANCED:
        ix=ids['train']; domain=np.where(y[ix,6]>.5,2,np.where(mask[ix,0]>.5,0,1))
        counts=np.bincount(domain); sampling=torch.tensor([1./counts[v] for v in domain],dtype=torch.double)
        loaders['train']=DataLoader(DS(x[ix],y[ix],mask[ix],True),batch_size=32,
                                  sampler=WeightedRandomSampler(sampling,len(ix),replacement=True))
    m=model(); baseline=model()
    vp,vy=predict(baseline,loaders['val']); base_val=evaluate(vp,vy,mask[ids['val']],.99)
    save('baseline_validation.json',base_val)
    if CONSTRAINED or BALANCED:
        baseline.eval()
        for p in baseline.parameters(): p.requires_grad_(False)
    if CONSTRAINED:
        for block in [m.features,m.classifier[0]]:
            for p in block.parameters(): p.requires_grad_(False)
    pos=(y[ids['train']]*mask[ids['train']]).sum(0); neg=mask[ids['train']].sum(0)-pos
    weights=torch.tensor(np.clip(neg/np.maximum(pos,1),1,8),dtype=torch.float32)
    criterion=nn.BCEWithLogitsLoss(pos_weight=weights,reduction='none')
    opt=torch.optim.AdamW([{'params':m.features.parameters(),'lr':1e-5},
       {'params':m.classifier[0].parameters(),'lr':5e-5},
       {'params':m.classifier[3].disease.parameters(),'lr':5e-5},
       {'params':m.classifier[3].normal.parameters(),'lr':1e-3}],weight_decay=1e-4)
    best_score=-1; best_epoch=0; history=[]
    for epoch in range(1,13):
        started=time.time(); m.train()
        for layer in m.modules():
            if isinstance(layer,nn.BatchNorm2d): layer.eval()
            if BALANCED and isinstance(layer,nn.Dropout): layer.eval()
        total=0; seen=0
        for xb,yb,mb in loaders['train']:
            opt.zero_grad(); logits=m(xb); loss=(criterion(logits,yb)*mb).sum()/mb.sum()
            if CONSTRAINED or BALANCED:
                disease_rows=(yb[:,6]<.5).float()[:,None]
                with torch.no_grad(): reference=baseline(xb)[:,:6]
                observed=disease_rows*mb[:,:6]
                # Preserve existing disease outputs on all observed disease labels.
                distill=((logits[:,:6]-reference).square()*observed).sum()/observed.sum().clamp_min(1)
                loss=loss+(0.1 if BALANCED else 0.4)*distill
            loss.backward(); nn.utils.clip_grad_norm_(m.parameters(),5.); opt.step()
            total+=float(loss.detach())*len(xb); seen+=len(xb)
        vp,vy=predict(m,loaders['val']); _,t=choose_threshold(vp,vy)
        result=evaluate(vp,vy,mask[ids['val']],t)
        score=np.mean([result['old']['macro_f1'],result['new']['macro_f1'],result['normal']['f1']])
        eligible=all(result[k]['macro_f1']>=base_val[k]['macro_f1']-(.02 if BALANCED else .005 if CONSTRAINED else .03) for k in ['old','new'])
        row=dict(epoch=epoch,loss=total/seen,seconds=time.time()-started,eligible=eligible,score=float(score),validation=result)
        history.append(row); save('training_history.json',history); print('EPOCH',json.dumps(row,ensure_ascii=False),flush=True)
        if eligible and score>best_score:
            best_score=float(score); best_epoch=epoch
            torch.save(dict(state_dict=m.state_dict(),classes=CLASSES,thresholds=OLD_THRESH.tolist()+[t],selected_epoch=epoch),OUT/'best7_extended.pt')
        if best_epoch and epoch-best_epoch>=4: break
    if not best_epoch: raise RuntimeError('No candidate passed disease retention validation; do not deploy.')
    best=torch.load(OUT/'best7_extended.pt',map_location='cpu',weights_only=False); m.load_state_dict(best['state_dict'])
    p,yy=predict(m,loaders['test']); result=evaluate(p,yy,mask[ids['test']],best['thresholds'][6])
    baseline=model(); bp,by=predict(baseline,loaders['test']); br=evaluate(bp,by,mask[ids['test']],.99)
    np.savez(OUT/'test_predictions.npz',probabilities=p,labels=yy,indices=ids['test'],baseline=bp)
    save('metrics7.json',dict(base_checkpoint=str(BASE),selected_epoch=best_epoch,thresholds=best['thresholds'],baseline_test=br,test=result,history=history))
    state=m.state_dict(); w=torch.cat((state.pop('classifier.3.disease.weight'),state.pop('classifier.3.normal.weight'))); b=torch.cat((state.pop('classifier.3.disease.bias'),state.pop('classifier.3.normal.bias')))
    state['classifier.3.weight']=w; state['classifier.3.bias']=b
    torch.save(dict(state_dict=state,classes=CLASSES,thresholds=best['thresholds'],input_size=160,selected_epoch=best_epoch),OUT/'best_normal7.pt')
    print('TEST',json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
