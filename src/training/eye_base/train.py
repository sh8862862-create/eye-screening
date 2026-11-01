"""Train and evaluate on the fixed grouped split prepared by prepare_data.py.

Examples (run from the parent of eye_training):
    python -m eye_training.train --task classifier --output eye_training/runs/classifier
    python -m eye_training.train --task localizer --output eye_training/runs/localizer

Selection uses validation data only. Test data are evaluated once after selection.
Localizer metrics describe agreement with bounding rectangles, not lesion masks.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset
from sklearn.metrics import average_precision_score, roc_auc_score

from .models import (CLASSES, LOCALIZER_CHANNELS, IMAGENET_MEAN, IMAGENET_STD,
                     MobileUNet, build_classifier)

ROOT = Path(__file__).resolve().parent


def validate_cache(data):
    required = ("images_cls", "images_seg", "labels", "splits", "groups", "box_masks", "mask_valid")
    for key in required:
        if key not in data:
            raise ValueError(f"Dataset cache missing {key}")
    n = len(data["labels"])
    if not n or any(len(data[key]) != n for key in required):
        raise ValueError("Dataset fields must have equal nonzero sample counts")
    for key, shape in [("images_cls", (n, 160, 160, 3)), ("images_seg", (n, 128, 128, 3)),
                       ("box_masks", (n, 2, 128, 128)), ("mask_valid", (n, 2)), ("labels", (n, 3))]:
        if data[key].shape != shape:
            raise ValueError(f"{key} shape must be {shape}, got {data[key].shape}")
    if not np.isin(data["labels"], [0, 1]).all():
        raise ValueError("Labels must be a three-channel binary multilabel matrix")
    if set(data["splits"].tolist()) != {"train", "val", "test"}:
        raise ValueError("Exactly three nonempty splits train, val, test are required")
    seen = {}
    for group, split in zip(data["groups"], data["splits"]):
        if group in seen and seen[group] != split:
            raise ValueError(f"Group leakage across splits: {group}")
        seen[group] = split
    training = data["labels"][data["splits"] == "train"]
    if np.any(training.sum(axis=0) == 0) or np.any(training.sum(axis=0) == len(training)):
        raise ValueError("Training split must contain positive and negative examples of every class")


class EyeDataset(Dataset):
    def __init__(self, data, split, task, augment=False):
        self.data = data
        self.task = task
        selected = data["splits"] == split
        # An image without any real box has no supervised localizer objective.
        if task == "localizer":
            selected &= data["mask_valid"].any(axis=1)
        self.indices = np.flatnonzero(selected)
        self.augment = augment
        self.mean = torch.tensor(IMAGENET_MEAN).view(3, 1, 1)
        self.std = torch.tensor(IMAGENET_STD).view(3, 1, 1)

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        i = self.indices[index]
        key = "images_cls" if self.task == "classifier" else "images_seg"
        x = torch.from_numpy(self.data[key][i]).permute(2, 0, 1).float().div(255)
        mask = torch.from_numpy(self.data["box_masks"][i]).float()
        if self.augment:
            if torch.rand(()) < .5:
                x = torch.flip(x, (-1,))
                mask = torch.flip(mask, (-1,))
            brightness = .90 + .20 * torch.rand(())
            contrast = .90 + .20 * torch.rand(())
            center = x.mean(dim=(1, 2), keepdim=True)
            x = ((x - center) * contrast + center).mul(brightness).clamp(0, 1)
        return {"image": (x - self.mean) / self.std,
                "label": torch.from_numpy(self.data["labels"][i].copy()).float(),
                "mask": mask,
                "valid": torch.from_numpy(self.data["mask_valid"][i].copy()).bool(),
                "index": torch.tensor(int(i), dtype=torch.long)}


def weak_box_loss(logits, target, valid):
    """BCE + soft Dice averaged only over genuinely annotated image/channels."""
    logits = logits.float()
    target = target.float()
    bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none").mean((2, 3))
    probability = logits.sigmoid()
    intersection = (probability * target).sum((2, 3))
    dice_loss = 1 - (2 * intersection + 1) / (probability.sum((2, 3)) + target.sum((2, 3)) + 1)
    weight = valid.to(logits.dtype)
    return ((bce + dice_loss) * weight).sum() / weight.sum().clamp_min(1)


def classification_metrics(labels, probabilities, thresholds=None):
    """Independent disease metrics; multiple positive labels are preserved."""
    labels, probabilities = np.asarray(labels, dtype=int), np.asarray(probabilities)
    thresholds = [.5] * 3 if thresholds is None else list(thresholds)
    predictions = (probabilities >= np.asarray(thresholds)).astype(int)
    per_class, f1s, totals = {}, [], np.zeros(3, dtype=int)
    for i, name in enumerate(CLASSES):
        truth, pred = labels[:, i], predictions[:, i]
        tp = int(((truth == 1) & (pred == 1)).sum())
        fp = int(((truth == 0) & (pred == 1)).sum())
        fn = int(((truth == 1) & (pred == 0)).sum())
        tn = int(((truth == 0) & (pred == 0)).sum())
        support, predicted = tp + fn, tp + fp
        recall = tp / support if support else 0.
        precision = tp / predicted if predicted else 0.
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.
        per_class[name] = {"precision": precision, "recall": recall, "f1": f1, "support": support,
                           "confusion_matrix": [[tn, fp], [fn, tp]],
                           "auc": float(roc_auc_score(truth, probabilities[:, i])) if 0 < support < len(truth) else None,
                           "average_precision": float(average_precision_score(truth, probabilities[:, i])) if support else None}
        f1s.append(f1)
        totals += [tp, fp, fn]
    tp, fp, fn = totals.tolist()
    return {"exact_match_accuracy": float(np.mean(np.all(labels == predictions, axis=1))),
            "macro_f1": float(np.mean(f1s)), "micro_f1": 2 * tp / max(2 * tp + fp + fn, 1),
            "confusion_matrix_axes": "per disease: rows=truth [absent,present], columns=prediction [absent,present]",
            "thresholds": thresholds,
            "classes": CLASSES, "per_class": per_class, "samples": len(labels)}


def select_thresholds(labels, probabilities):
    """Calibrate each disease on validation only; ties prefer closer to 0.5."""
    labels, probabilities = np.asarray(labels, dtype=int), np.asarray(probabilities)
    grid = [round(.10 + .05 * index, 2) for index in range(17)]
    result = []
    for channel in range(3):
        # Without both positive and negative validation cases, avoid unsupported fitting.
        if len(np.unique(labels[:, channel])) < 2:
            result.append(.5)
            continue
        options = []
        truth = labels[:, channel].astype(bool)
        for threshold in grid:
            pred = probabilities[:, channel] >= threshold
            tp = (truth & pred).sum()
            fp, fn = (~truth & pred).sum(), (truth & ~pred).sum()
            f1 = 2 * tp / max(2 * tp + fp + fn, 1)
            options.append((float(f1), -abs(threshold - .5), threshold))
        result.append(max(options)[2])
    return result


@torch.inference_mode()
def evaluate(model, loader, task, device, criterion=None, thresholds=None):
    model.eval()
    labels, predictions, probabilities, indices = [], [], [], []
    dice_values = [[], []]
    loss_sum, samples = 0., 0
    for batch in loader:
        x = batch["image"].to(device)
        logits = model(x)
        n = len(x)
        if task == "classifier":
            y = batch["label"].to(device)
            loss = F.binary_cross_entropy_with_logits(logits, y)
            probs = logits.sigmoid().cpu().numpy()
            labels.extend(batch["label"].tolist())
            predictions.extend((probs >= np.asarray(thresholds or [.5] * 3)).astype(int).tolist())
            probabilities.extend(probs.tolist())
        else:
            target, valid = batch["mask"].to(device), batch["valid"].to(device)
            loss = weak_box_loss(logits, target, valid)
            pred = (logits.sigmoid() >= .5).float()
            dice = (2 * (pred * target).sum((2, 3)) + 1e-6) / (pred.sum((2, 3)) + target.sum((2, 3)) + 1e-6)
            for channel in range(2):
                dice_values[channel].extend(dice[:, channel][valid[:, channel]].cpu().tolist())
        indices.extend(batch["index"].tolist())
        loss_sum += loss.item() * n
        samples += n
    if not samples:
        raise ValueError(f"No usable examples for {task} evaluation")
    if task == "classifier":
        metrics = classification_metrics(labels, probabilities, thresholds)
        metrics.update(labels=labels, predictions=predictions, probabilities=probabilities)
    else:
        values = [value for channel in dice_values for value in channel]
        metrics = {"weak_box_dice": float(np.mean(values)),
                   "per_channel": {name: {"weak_box_dice": float(np.mean(values)) if values else None,
                                          "annotated_samples": len(values)}
                                   for name, values in zip(LOCALIZER_CHANNELS, dice_values)},
                   "threshold": .5, "samples": samples,
                   "interpretation": "Agreement with bounding rectangles; not lesion segmentation. Unannotated channels are ignored."}
    metrics.update(loss=loss_sum / samples, sample_indices=indices)
    return metrics


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def atomic_checkpoint(path, checkpoint):
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(checkpoint, temporary)
    temporary.replace(path)


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task", choices=["classifier", "localizer"], required=True)
    p.add_argument("--data", type=Path, default=ROOT / "data" / "dataset.npz")
    p.add_argument("--output", type=Path)
    p.add_argument("--epochs", type=int)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default="auto")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--patience", type=int, default=8)
    p.add_argument("--lr", type=float)
    p.add_argument("--weight-decay", type=float, default=1e-4)
    p.add_argument("--head-epochs", type=int, default=3)
    p.add_argument("--no-pretrained", action="store_true", help="For reproducible smoke tests; real classifier training defaults to ImageNet weights")
    p.add_argument("--no-amp", action="store_true")
    p.add_argument("--resume", action="store_true", help="Resume last.pt in the SAME run with identical options/data")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    args.epochs = args.epochs or (25 if args.task == "classifier" else 30)
    args.lr = args.lr or (1e-3 if args.task == "classifier" else 2e-3)
    if args.epochs < 1 or args.batch_size < 1 or args.patience < 1 or args.threads < 1:
        raise ValueError("epochs, batch-size, patience and threads must be positive")
    if args.output is None:
        if args.resume:
            raise ValueError("--resume requires --output")
        args.output = ROOT / "runs" / f"{args.task}_{datetime.now():%Y%m%d_%H%M%S}"
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise FileExistsError(f"Refusing to overwrite {output}; use a new directory or --resume")
    if args.resume and not (output / "last.pt").exists():
        raise FileNotFoundError("--resume requires an existing last.pt")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else torch.device(args.device)
    torch.set_num_threads(args.threads)
    seed_everything(args.seed)
    with np.load(args.data, allow_pickle=False) as raw:
        data = {key: raw[key] for key in raw.files}
    validate_cache(data)
    datasets = {split: EyeDataset(data, split, args.task, augment=split == "train") for split in ("train", "val", "test")}
    if any(not len(dataset) for dataset in datasets.values()):
        raise ValueError("Each split must contain supervised examples for this task")
    generator = torch.Generator().manual_seed(args.seed)
    loaders = {split: DataLoader(dataset, batch_size=args.batch_size, shuffle=split == "train",
                                num_workers=0, pin_memory=device.type == "cuda", generator=generator)
               for split, dataset in datasets.items()}
    config = {key: str(value.resolve()) if isinstance(value, Path) else value for key, value in vars(args).items()}
    config["resolved_device"] = str(device)
    config["torch_version"] = str(torch.__version__)
    with args.data.open("rb") as handle:
        config["dataset_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    config["split_samples"] = {split: len(dataset) for split, dataset in datasets.items()}
    model = build_classifier(pretrained=not args.no_pretrained and not args.resume) if args.task == "classifier" else MobileUNet()
    model.to(device)
    training_labels = data["labels"][data["splits"] == "train"]
    counts = training_labels.sum(axis=0)
    weights = torch.tensor((len(training_labels) - counts) / counts, dtype=torch.float32, device=device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=weights)
    if args.task == "classifier":
        optimizer = torch.optim.AdamW([{"params": model.features.parameters(), "lr": args.lr * .1},
                                      {"params": model.classifier.parameters(), "lr": args.lr}],
                                     weight_decay=args.weight_decay)
    else:
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    amp_enabled = device.type == "cuda" and not args.no_amp
    scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled)
    start_epoch, best_score, bad_epochs, history = 0, -1., 0, []
    if args.resume:
        state = torch.load(output / "last.pt", map_location="cpu", weights_only=False)
        saved = state["training_config"]
        for key in ("task", "epochs", "batch_size", "seed", "lr", "weight_decay", "head_epochs", "no_pretrained", "dataset_sha256"):
            if config[key] != saved[key]:
                raise ValueError(f"Resume configuration differs for {key}; start a new run")
        model.load_state_dict(state["state_dict"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        scaler.load_state_dict(state["scaler"])
        start_epoch, best_score, bad_epochs, history = state["epoch"] + 1, state["best_score"], state["bad_epochs"], state["history"]
        torch.set_rng_state(state["torch_rng"])
        np.random.set_state(state["numpy_rng"])
        random.setstate(state["python_rng"])
        generator.set_state(state["loader_rng"])
        if device.type == "cuda" and state["cuda_rng"] is not None:
            torch.cuda.set_rng_state_all(state["cuda_rng"])
    output.mkdir(parents=True, exist_ok=True)
    config["positive_class_weights"] = weights.cpu().tolist()
    atomic_json(output / "config.json", config)
    num_params = sum(parameter.numel() for parameter in model.parameters())
    print(json.dumps({"task": args.task, "device": str(device), "parameters": num_params,
                      "split_samples": config["split_samples"], "amp": amp_enabled}), flush=True)
    for epoch in range(start_epoch, args.epochs):
        if bad_epochs >= args.patience:
            break
        began = time.perf_counter()
        model.train()
        frozen = args.task == "classifier" and epoch < args.head_epochs
        if args.task == "classifier":
            for parameter in model.features.parameters():
                parameter.requires_grad_(not frozen)
            if frozen:
                model.features.eval()
        train_loss, train_count = 0., 0
        for batch in loaders["train"]:
            x = batch["image"].to(device)
            optimizer.zero_grad(set_to_none=True)
            context = torch.autocast(device_type="cuda", dtype=torch.float16) if amp_enabled else nullcontext()
            with context:
                logits = model(x)
                loss = criterion(logits, batch["label"].to(device)) if args.task == "classifier" else weak_box_loss(logits, batch["mask"].to(device), batch["valid"].to(device))
            if not torch.isfinite(loss):
                raise FloatingPointError(f"Nonfinite training loss at epoch {epoch + 1}")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            scaler.step(optimizer)
            scaler.update()
            train_loss += loss.item() * len(x)
            train_count += len(x)
        validation = evaluate(model, loaders["val"], args.task, device, criterion)
        selection_metric = "macro_f1" if args.task == "classifier" else "weak_box_dice"
        score = validation[selection_metric]
        row = {"epoch": epoch + 1, "train_loss": train_loss / train_count, "val_loss": validation["loss"],
               f"val_{selection_metric}": score, "lr": [group["lr"] for group in optimizer.param_groups],
               "head_only": frozen, "seconds": time.perf_counter() - began}
        if args.task == "classifier":
            row["val_exact_match_accuracy"] = validation["exact_match_accuracy"]
            row["val_micro_f1"] = validation["micro_f1"]
        history.append(row)
        improved = score > best_score + 1e-8
        best_score, bad_epochs = (score, 0) if improved else (best_score, bad_epochs + 1)
        checkpoint = {"state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
                      "architecture": "mobilenet_v3_small" if args.task == "classifier" else "mobile_unet",
                      "classes": CLASSES if args.task == "classifier" else LOCALIZER_CHANNELS,
                      "image_size": 160 if args.task == "classifier" else 128,
                      "params": num_params, "epoch": epoch, "selected_epoch": epoch + 1,
                      "selection_metric": selection_metric, "selection_score": score,
                      "training_config": config, "normalization": {"mean": IMAGENET_MEAN, "std": IMAGENET_STD},
                      "supervision": "image_multilabel" if args.task == "classifier" else "weak_bounding_rectangle",
                      "output_activation": "sigmoid", "thresholds": [.5] * (3 if args.task == "classifier" else 2)}
        if improved:
            atomic_checkpoint(output / "best.pt", checkpoint)
            atomic_json(output / "val_metrics.json", validation)
        scheduler.step()
        checkpoint.update(optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(), scaler=scaler.state_dict(),
                          best_score=best_score, bad_epochs=bad_epochs, history=history,
                          torch_rng=torch.get_rng_state(), numpy_rng=np.random.get_state(), python_rng=random.getstate(),
                          loader_rng=generator.get_state(), cuda_rng=torch.cuda.get_rng_state_all() if device.type == "cuda" else None)
        atomic_checkpoint(output / "last.pt", checkpoint)
        atomic_json(output / "history.json", history)
        print(json.dumps(row), flush=True)
    selected = torch.load(output / "best.pt", map_location="cpu", weights_only=False)
    model.load_state_dict(selected["state_dict"])
    thresholds = None
    if args.task == "classifier":
        validation = evaluate(model, loaders["val"], args.task, device, criterion)
        thresholds = select_thresholds(validation["labels"], validation["probabilities"])
        validation.update(classification_metrics(validation["labels"], validation["probabilities"], thresholds))
        validation["predictions"] = (np.asarray(validation["probabilities"]) >= np.asarray(thresholds)).astype(int).tolist()
        validation["threshold_selection"] = "per-class F1 grid 0.10 to 0.90 step 0.05 on validation only; checkpoint selected first at 0.5"
        selected["thresholds"] = thresholds
        selected["threshold_selection"] = validation["threshold_selection"]
        atomic_checkpoint(output / "best.pt", selected)
        atomic_json(output / "val_metrics.json", validation)
    # The held-out test set is first accessed by model evaluation here, after selection.
    test_metrics = evaluate(model, loaders["test"], args.task, device, criterion, thresholds=thresholds)
    test_metrics.update(selected_epoch=selected["selected_epoch"], selection_split="val", split="test")
    atomic_json(output / "test_metrics.json", test_metrics)
    summary = {"task": args.task, "selected_epoch": selected["selected_epoch"],
               "validation_selection_score": selected["selection_score"], "test_metrics": str(output / "test_metrics.json"),
               "best_checkpoint": str(output / "best.pt")}
    atomic_json(output / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    return summary


if __name__ == "__main__":
    main()
