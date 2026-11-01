"""Conservative six-class fine-tune starting from the board model checkpoint.

The merged archive contains old-domain images with labels for classes 0..2 and
new-domain images with labels for classes 3..5.  The per-image observation mask
keeps the other outputs out of the loss instead of treating them as negatives.
"""
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from torchvision.models import mobilenet_v3_small
from torchvision.transforms import v2

WORK = Path(os.environ.get('EYE_TRAINING_ROOT', Path(__file__).resolve().parents[2] / 'data/training'))
BASE = WORK / "runs" / "best6.pt"
OUT = Path(__file__).parent / "runs_all6_finetune"
CLASSES = ["pinguecula", "pterygium", "subconjunctival_hemorrhage",
           "hordeolum", "blepharitis", "dacryocystitis"]
THRESH = np.asarray([.80, .55, .55, .50, .50, .50], dtype=np.float32)
SIZE = 160
OLD_COUNT = 1414
SEED = 20261023
EPOCHS = 12
PATIENCE = 4
BATCH = 32
FEATURE_LR = 1e-5
HEAD_LR = 5e-5
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


def seed_all():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)


class DS(Dataset):
    def __init__(self, x, y, mask, train):
        self.x, self.y, self.mask, self.train = x, y, mask, train
        self.train_tf = v2.Compose([
            v2.RandomHorizontalFlip(),
            v2.RandomAffine(degrees=6, translate=(.03, .03), scale=(.95, 1.05)),
            v2.ColorJitter(.12, .12, .12, .015),
            v2.ToDtype(torch.float32, scale=True),
            v2.Normalize(MEAN, STD),
        ])
        self.eval_tf = v2.Compose([
            v2.ToDtype(torch.float32, scale=True),
            v2.Normalize(MEAN, STD),
        ])

    def __len__(self):
        return len(self.x)

    def __getitem__(self, i):
        image = torch.from_numpy(self.x[i]).permute(2, 0, 1)
        image = self.train_tf(image) if self.train else self.eval_tf(image)
        return image, torch.from_numpy(self.y[i]), torch.from_numpy(self.mask[i])


def make_model():
    model = mobilenet_v3_small(weights=None)
    model.classifier[3] = nn.Linear(model.classifier[3].in_features, len(CLASSES))
    checkpoint = torch.load(BASE, map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["state_dict"])
    return model


@torch.inference_mode()
def predict(model, loader):
    model.eval()
    probs, labels = [], []
    for xb, yb, _ in loader:
        probs.append(torch.sigmoid(model(xb)).numpy())
        labels.append(yb.numpy())
    return np.concatenate(probs), np.concatenate(labels)


def auc(prob, truth):
    pos, neg = prob[truth], prob[~truth]
    if not len(pos) or not len(neg):
        return None
    delta = pos[:, None] - neg[None, :]
    return float((delta > 0).mean() + .5 * (delta == 0).mean())


def report(prob, labels, classes):
    rows, aucs = [], []
    for c in classes:
        truth = labels[:, c] > .5
        pred = prob[:, c] >= THRESH[c]
        tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum())
        fn = int((~pred & truth).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        value = auc(prob[:, c], truth)
        if value is not None:
            aucs.append(value)
        rows.append({"class": CLASSES[c], "support": int(truth.sum()),
                     "precision": round(precision, 3), "recall": round(recall, 3),
                     "f1": round(f1, 3), "auc": None if value is None else round(value, 4)})
    return {"mean_auc": round(float(np.mean(aucs)), 4) if aucs else None,
            "macro_f1": round(float(np.mean([r["f1"] for r in rows])), 4),
            "per_class": rows}


def domain_report(prob, labels, mask):
    old = mask[:, 0] > .5
    new = ~old
    return {"old": report(prob[old], labels[old], [0, 1, 2]),
            "new": report(prob[new], labels[new], [3, 4, 5])}


def main():
    seed_all()
    torch.set_num_threads(min(8, torch.get_num_threads()))
    OUT.mkdir(parents=True, exist_ok=True)
    data = np.load(WORK / "dataset6.npz", allow_pickle=True)
    x, y, split = data["images_cls"], data["labels"], data["splits"]
    mask = np.zeros_like(y, dtype=np.float32)
    mask[:OLD_COUNT, :3] = 1.0
    mask[OLD_COUNT:, 3:] = 1.0
    train = np.where(split == "train")[0]
    val = np.where(split == "val")[0]
    test = np.where(split == "test")[0]
    print("all six classes; samples", {s: int((split == s).sum()) for s in ("train", "val", "test")}, flush=True)

    observed = y[train] * mask[train]
    positives = observed.sum(0)
    negatives = mask[train].sum(0) - positives
    pos_weight = torch.tensor(np.clip(negatives / np.maximum(positives, 1), 1, 8), dtype=torch.float32)
    print("pos_weight", pos_weight.tolist(), flush=True)
    train_loader = DataLoader(DS(x[train], y[train], mask[train], True), batch_size=BATCH, shuffle=True)
    val_loader = DataLoader(DS(x[val], y[val], mask[val], False), batch_size=BATCH)
    test_loader = DataLoader(DS(x[test], y[test], mask[test], False), batch_size=BATCH)

    model = make_model()
    optimizer = torch.optim.AdamW([
        {"params": model.features.parameters(), "lr": FEATURE_LR},
        {"params": model.classifier.parameters(), "lr": HEAD_LR},
    ], weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight, reduction="none")
    best_score, best_epoch, best_state = -1.0, 0, None
    history = []
    for epoch in range(1, EPOCHS + 1):
        model.train(); total = 0.0; seen = 0; started = time.time()
        for xb, yb, mb in train_loader:
            optimizer.zero_grad()
            loss = (loss_fn(model(xb), yb) * mb).sum() / mb.sum().clamp_min(1)
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * len(yb); seen += len(yb)
        scheduler.step()
        vp, vy = predict(model, val_loader)
        vr = domain_report(vp, vy, mask[val])
        score = float(np.mean([vr["old"]["mean_auc"], vr["new"]["mean_auc"]]))
        row = {"epoch": epoch, "loss": round(total / max(seen, 1), 4),
               "val_domain_mean_auc": round(score, 4),
               "old_val": vr["old"], "new_val": vr["new"],
               "seconds": round(time.time() - started, 1)}
        history.append(row); print("epoch", row, flush=True)
        if score > best_score:
            best_score, best_epoch = score, epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if epoch - best_epoch >= PATIENCE:
            break

    model.load_state_dict(best_state)
    tp, ty = predict(model, test_loader)
    test_report = domain_report(tp, ty, mask[test])
    checkpoint = {"state_dict": model.state_dict(), "classes": CLASSES,
                  "thresholds": THRESH.tolist(), "input_size": SIZE,
                  "selected_epoch": best_epoch,
                  "training": "fine_tune_board_model_on_all_six_classes_masked"}
    torch.save(checkpoint, OUT / "best_all6_finetuned.pt")
    report = {"base_checkpoint": str(BASE), "dataset": str(WORK / "dataset6.npz"),
              "counts": {s: int((split == s).sum()) for s in ("train", "val", "test")},
              "selected_epoch": best_epoch, "history": history, "test": test_report}
    (OUT / "metrics_all6_finetuned.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("TEST", json.dumps(test_report, ensure_ascii=False), flush=True)
    print("saved", OUT / "best_all6_finetuned.pt", flush=True)


if __name__ == "__main__":
    main()
