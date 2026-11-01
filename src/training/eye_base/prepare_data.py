"""Prepare immutable grouped splits and small RGB caches; never alter source images."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from sklearn.model_selection import GroupShuffleSplit

LABELS = ('pinguecula', 'pterygium', 'subconjunctival_hemorrhage')
HERE = Path(__file__).resolve().parent


def row_labels(row):
    return row.get('positive_labels') or [row['class_name']]


def assign_splits(rows, seed=20260914):
    """Choose one deterministic grouped split by label balance only, never model results."""
    labels = np.array([[name in row_labels(r) for name in LABELS] for r in rows], dtype=np.float32)
    groups = np.array([r['split_group'] for r in rows])
    indices = np.arange(len(rows))
    best = None
    for attempt in range(100):
        train, hold = next(GroupShuffleSplit(n_splits=1, test_size=.30, random_state=seed+attempt).split(indices, groups=groups))
        va, te = next(GroupShuffleSplit(n_splits=1, test_size=.5, random_state=seed+1000+attempt).split(hold, groups=groups[hold]))
        parts = [train, hold[va], hold[te]]
        if any((labels[p].sum(0) == 0).any() for p in parts):
            continue
        score = sum(np.abs(labels[p].sum(0) / labels.sum(0) - fraction).sum() + abs(len(p) / len(rows) - fraction) for p, fraction in zip(parts, [.7,.15,.15]))
        if best is None or score < best[0]:
            best = (score, parts)
    if best is None:
        raise ValueError('Insufficient independent groups for all labels in train/val/test')
    result = np.empty(len(rows), dtype='U5')
    for p, name in zip(best[1], ('train','val','test')):
        result[p] = name
    return result.tolist()


def geometry(width, height, size):
    scale = size / max(width, height)
    w, h = max(1, min(size, round(width*scale))), max(1, min(size, round(height*scale)))
    return w, h, (size-w)//2, (size-h)//2


def letterbox_rgb(image, size):
    height, width = image.shape[:2]
    w, h, left, top = geometry(width, height, size)
    output = np.full((size,size,3), 114, dtype=np.uint8)
    output[top:top+h,left:left+w] = cv2.resize(image,(w,h),interpolation=cv2.INTER_LINEAR)
    return output


def box_targets(row, size):
    width, height = row['width'], row['height']
    w, h, left, top = geometry(width,height,size)
    masks = np.zeros((2,size,size), dtype=np.uint8)
    valid = np.zeros(2,dtype=bool)
    for c, name in enumerate(LABELS[:2]):
        boxes = (row.get('source_boxes') or {}).get(name, [])
        for box in boxes:
            x,y,bw,bh = [float(box[key]) for key in ('x','y','width','height')]
            if not np.isfinite([x,y,bw,bh]).all() or bw <= 0 or bh <= 0 or x < 0 or y < 0 or x+bw > width+1 or y+bh > height+1:
                raise ValueError(f'Invalid source box: {box}')
            x0,y0 = int(np.floor(x*w/width))+left, int(np.floor(y*h/height))+top
            x1,y1 = int(np.ceil((x+bw)*w/width))+left, int(np.ceil((y+bh)*h/height))+top
            masks[c,max(0,y0):min(size,y1),max(0,x0):min(size,x1)] = 1
            valid[c] = True
    return masks, valid


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('C:/Users/19883/Desktop/眼病训练'))
    parser.add_argument('--manifest',type=Path,default=HERE/'audit/unique_manifest.json')
    parser.add_argument('--output',type=Path,default=HERE/'data')
    args=parser.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=True)
    rows=json.loads(args.manifest.read_text(encoding='utf-8'))
    if any(r.get('decode_error') for r in rows):
        raise ValueError('Corrupt images must be reviewed before preparing data')
    if len({r['sha256'] for r in rows}) != len(rows):
        raise ValueError('Manifest must contain unique images')
    splits=assign_splits(rows)
    def load(row):
        path=args.root/row['relative_path']
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:
            raise ValueError(f'Source changed since audit: {path}')
        bgr=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
        if bgr is None or bgr.shape[:2] != (row['height'],row['width']):
            raise ValueError(f'Unexpected image geometry: {path}')
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        mask, valid=box_targets(row,128)
        return letterbox_rgb(rgb,160),letterbox_rgb(rgb,128),mask,valid
    cv2.setNumThreads(1)
    with ThreadPoolExecutor(max_workers=6) as pool:
        arrays=list(pool.map(load,rows))
    for row,split in zip(rows,splits):
        row['split']=split
    labels=np.array([[name in row_labels(r) for name in LABELS] for r in rows],dtype=np.float32)
    manifest_text=json.dumps(rows,ensure_ascii=False,indent=2)
    (args.output/'manifest.json').write_text(manifest_text,encoding='utf-8')
    np.savez_compressed(args.output/'dataset.npz', images_cls=np.stack([a[0] for a in arrays]), images_seg=np.stack([a[1] for a in arrays]), box_masks=np.stack([a[2] for a in arrays]), mask_valid=np.stack([a[3] for a in arrays]), labels=labels, splits=np.array(splits), groups=np.array([r['split_group'] for r in rows]), paths=np.array([r['relative_path'] for r in rows]))
    summary={'seed':20260914,'root':str(args.root.resolve()),'label_order':list(LABELS),'task':'multilabel','unique_images':len(rows),'groups':len({r['split_group'] for r in rows}),'manifest_sha256':hashlib.sha256(manifest_text.encode('utf-8')).hexdigest(),'splits':{},'limitations':['No normal/other disease class','Absent label treated as negative for training but not clinically adjudicated','No patient IDs; family and duplicate grouping only','No pixel masks; only rectangular weak supervision for first two classes']}
    for split in ('train','val','test'):
        idx=[i for i,s in enumerate(splits) if s==split]
        selected=[rows[i] for i in idx]
        summary['splits'][split]={'images':len(idx),'groups':len({r['split_group'] for r in selected}),'positive_labels':dict(zip(LABELS,labels[idx].sum(0).astype(int).tolist())),'annotated_images':sum(bool(arrays[i][3].any()) for i in idx),'annotated_channels':np.stack([arrays[i][3] for i in idx]).sum(0).tolist(),'sources':dict(Counter(r['source'] for r in selected))}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
