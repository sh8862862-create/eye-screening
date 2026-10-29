#!/usr/bin/env python3
"""OpenCV DNN CPU inference. Python >= 3.6; no PyTorch or ONNX Runtime."""

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import cv2
import numpy as np


LABELS = (
    ("pinguecula", "睑裂斑"),
    ("pterygium", "翼状胬肉"),
    ("subconjunctival_hemorrhage", "结膜下出血"),
    ("hordeolum", "麦粒肿"),
    ("blepharitis", "睑缘炎"),
    ("dacryocystitis", "泪囊炎"),
    ("normal", "正常"),
)
DEFAULT_THRESHOLDS = [.80, .55, .55, .50, .50, .50, .99]
MEAN = np.asarray([.485, .456, .406], dtype=np.float32)
STD = np.asarray([.229, .224, .225], dtype=np.float32)
DEFAULT_MODELS = Path(__file__).resolve().parent.parent / "artifacts"


def read_image(path):
    """Decode Unicode paths on Windows and Linux; output BGR uint8."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("Image does not exist: {}".format(path))
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ValueError("Cannot decode image: {}".format(path))
    return image


def preprocess(image_bgr, size):
    """Centered INTER_LINEAR letterbox, fill 114, RGB/ImageNet normalization.

    Returns contiguous float32 NCHW and geometry for inverse mask mapping.
    round() and integer floor centering match the training implementation.
    """
    if not isinstance(size, (int, np.integer)) or size < 1:
        raise ValueError("Input size must be a positive integer")
    if (not isinstance(image_bgr, np.ndarray) or image_bgr.ndim != 3
            or image_bgr.shape[2] != 3 or min(image_bgr.shape[:2]) < 1
            or image_bgr.dtype != np.uint8):
        raise ValueError("Expected nonempty HxWx3 uint8 BGR image")
    original_height, original_width = image_bgr.shape[:2]
    scale = float(size) / max(original_height, original_width)
    width = max(1, min(size, int(round(original_width * scale))))
    height = max(1, min(size, int(round(original_height * scale))))
    left, top = (size - width) // 2, (size - height) // 2
    resized = cv2.resize(image_bgr, (width, height), interpolation=cv2.INTER_LINEAR)
    padded = np.full((size, size, 3), 114, dtype=np.uint8)
    padded[top:top + height, left:left + width] = resized
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / np.float32(255)
    tensor = np.ascontiguousarray(((rgb - MEAN) / STD).transpose(2, 0, 1)[None])
    geometry = dict(original_width=original_width, original_height=original_height,
                    size=size, left=left, top=top, width=width, height=height)
    return tensor, geometry


def preprocess_center(image_bgr, size):
    """Short-side resize + center crop, matching the seven-class training pipeline."""
    if not isinstance(size, (int, np.integer)) or size < 1:
        raise ValueError("Input size must be a positive integer")
    if (not isinstance(image_bgr, np.ndarray) or image_bgr.ndim != 3
            or image_bgr.shape[2] != 3 or min(image_bgr.shape[:2]) < 1
            or image_bgr.dtype != np.uint8):
        raise ValueError("Expected nonempty HxWx3 uint8 BGR image")
    height, width = image_bgr.shape[:2]
    scale = float(size) / min(height, width)
    resized = cv2.resize(image_bgr, (max(1, round(width * scale)), max(1, round(height * scale))),
                         interpolation=cv2.INTER_LINEAR)
    h, w = resized.shape[:2]
    if h < size or w < size:
        canvas = np.full((max(size, h), max(size, w), 3), 114, dtype=np.uint8)
        top, left = (canvas.shape[0] - h) // 2, (canvas.shape[1] - w) // 2
        canvas[top:top + h, left:left + w] = resized
        resized = canvas
        h, w = resized.shape[:2]
    top, left = (h - size) // 2, (w - size) // 2
    cropped = resized[top:top + size, left:left + size]
    rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB).astype(np.float32) / np.float32(255)
    return np.ascontiguousarray(((rgb - MEAN) / STD).transpose(2, 0, 1)[None])


def classify_logits(logits, thresholds=None):
    logits = np.asarray(logits, dtype=np.float64)
    count = len(LABELS)
    if logits.shape != (1, count) or not np.isfinite(logits).all():
        raise ValueError("Classifier must output finite logits shaped [1, {}]".format(count))
    thresholds = np.asarray(DEFAULT_THRESHOLDS if thresholds is None else thresholds, dtype=np.float64)
    if thresholds.shape != (count,) or not np.isfinite(thresholds).all() or not ((thresholds > 0) & (thresholds < 1)).all():
        raise ValueError("Classifier thresholds must be {} finite values between 0 and 1".format(count))
    # Independent sigmoid supports coexisting diseases; do not normalize across labels.
    probabilities = np.empty(count, dtype=np.float64)
    positive = logits[0] >= 0
    probabilities[positive] = 1 / (1 + np.exp(-logits[0, positive]))
    exp_negative = np.exp(logits[0, ~positive])
    probabilities[~positive] = exp_negative / (1 + exp_negative)
    disease_indices = np.flatnonzero(probabilities[:6] >= thresholds[:6]).tolist()
    normal_candidate = bool(probabilities[6] >= thresholds[6])
    normal_positive = normal_candidate and not disease_indices
    indices = disease_indices if disease_indices else ([6] if normal_positive else [])
    summary = max(indices, key=lambda i: probabilities[i]) if indices else int(np.argmax(probabilities))
    decision = 'disease' if disease_indices else 'normal' if normal_positive else 'uncertain' 
    return dict(
        class_id=summary, summary_only=True,
        status="normal" if normal_positive else "target_labels_above_threshold" if disease_indices else "uncertain",
        decision=decision, decision_label_zh='正常' if normal_positive else '检出眼病' if disease_indices else '未确定',
        disease_positive_class_ids=disease_indices, normal_positive=normal_positive,
        normal_conflict=normal_candidate and bool(disease_indices),
        positive_class_ids=indices, positive_labels=[LABELS[i][0] for i in indices],
        positive_labels_zh=[LABELS[i][1] for i in indices],
        score_type="uncalibrated_independent_sigmoid",
        scores=[dict(class_id=i, label=name, label_zh=zh, score=float(probabilities[i]),
                     threshold=float(thresholds[i]), positive=i in indices,
                     above_threshold=bool(probabilities[i] >= thresholds[i]),
                     suppressed=(i == 6 and normal_candidate and bool(disease_indices)))
                for i, (name, zh) in enumerate(LABELS)],
        note_zh="正常需正常类别达阈值且六种眼病均未达阈值；均未达阈值显示未确定。正常为模型分类结果，不能排除其他疾病。",
    )


def resolve_thresholds(classifier_path, explicit=None):
    """Return thresholds and their source; artifact config is optional for portability."""
    count = len(LABELS)
    if explicit is not None:
        classify_logits(np.zeros((1, count)), explicit)
        return [float(x) for x in explicit], "cli_override"
    config = Path(classifier_path).parent / "model_config.json"
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            values = data.get("classification_thresholds")
            if values is not None:
                classify_logits(np.zeros((1, count)), values)
                return [float(x) for x in values], str(config)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return list(DEFAULT_THRESHOLDS), "default_uncalibrated"


def localize_logits(logits, class_id, geometry, threshold=.5):
    """Undo letterbox and return a weakly supervised region for classes 0/1."""
    if not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("Localization threshold must be between 0 and 1")
    if class_id not in range(len(LABELS)):
        raise ValueError("Class id must be one of the known labels")
    if class_id >= 2:
        return None, dict(status="unsupported_class",
                          reason_zh="{}未提供定位标注".format(LABELS[class_id][1]))
    logits = np.asarray(logits, dtype=np.float32)
    size = geometry["size"]
    if logits.shape != (1, 2, size, size) or not np.isfinite(logits).all():
        raise ValueError("Localizer must output finite logits shaped [1, 2, size, size]")
    probability = 1 / (1 + np.exp(-np.clip(logits[0, class_id], -80, 80)))
    left, top = geometry["left"], geometry["top"]
    probability = probability[top:top + geometry["height"], left:left + geometry["width"]]
    probability = cv2.resize(probability, (geometry["original_width"], geometry["original_height"]),
                             interpolation=cv2.INTER_LINEAR)
    mask = (probability >= threshold).astype(np.uint8) * 255
    ys, xs = np.nonzero(mask)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
    return mask, dict(
        status="region_found" if bbox else "no_region_above_threshold",
        class_id=class_id, label=LABELS[class_id][0], label_zh=LABELS[class_id][1],
        annotation_type="weak_box_supervision", threshold=float(threshold),
        bbox_xyxy=bbox, bbox_convention="zero_based_xyxy_exclusive_max",
        area_fraction=float(np.count_nonzero(mask)) / mask.size,
        note_zh="区域由矩形框弱监督学习得到，不代表精确病灶轮廓或医学分割。",
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_net(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("ONNX model does not exist: {}".format(path))
    if not hasattr(cv2, "dnn") or not hasattr(cv2.dnn, "readNetFromONNX"):
        raise RuntimeError("This OpenCV build lacks the ONNX DNN importer; install an OpenCV build with DNN")
    try:
        net = cv2.dnn.readNetFromONNX(str(path))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        return net
    except cv2.error as error:
        raise RuntimeError("OpenCV {} cannot import {}. Use the validated OpenCV version or rebuild with DNN. {}".format(
            cv2.__version__, path.name, error)) from error


def forward(net, blob):
    start = time.perf_counter()
    net.setInput(blob)
    output = net.forward()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return output, elapsed_ms


def save_overlay(path, image, mask, bbox):
    output = image.copy()
    selected = mask > 0
    output[selected] = (.55 * output[selected] + .45 * np.asarray([0, 200, 255])).astype(np.uint8)
    if bbox:
        x1, y1, x2, y2 = bbox
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (0, 200, 255), max(1, min(output.shape[:2]) // 180))
    path = Path(path)
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Overlay extension must be .png, .jpg, or .jpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix.lower(), output)
    if not success:
        raise RuntimeError("Could not encode overlay")
    encoded.tofile(str(path))


def save_result_image(path, image, classification, regions=None):
    """Write an annotated image even when no localization channel is available.

    Classification-only predictions use a small information panel below the
    original image. Localized regions, when present, are outlined on the image.
    """
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Expected an HxWx3 image")
    path = Path(path)
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Result image extension must be .png, .jpg, or .jpeg")
    output = image.copy()
    for region in regions or ():
        bbox = region.get("bbox_xyxy") if isinstance(region, dict) else None
        if bbox and len(bbox) == 4:
            x1, y1, x2, y2 = [int(value) for value in bbox]
            cv2.rectangle(output, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)),
                          (0, 200, 255), max(1, min(output.shape[:2]) // 180))

    scores = classification.get("scores", []) if isinstance(classification, dict) else []
    positive_labels = classification.get("positive_labels", []) if isinstance(classification, dict) else []
    summary = ", ".join(positive_labels) if positive_labels else "no target label above threshold"
    panel_height = 30 + 20 * (len(scores) + 1)
    canvas = cv2.copyMakeBorder(output, 0, panel_height, 0, 0,
                                cv2.BORDER_CONSTANT, value=(245, 245, 245))
    text_x = 6
    base_y = image.shape[0] + 19
    cv2.putText(canvas, "Eye model result | " + summary[:90], (text_x, base_y),
                cv2.FONT_HERSHEY_SIMPLEX, .38, (30, 30, 30), 1, cv2.LINE_AA)
    for index, score in enumerate(scores):
        label = str(score.get("label", "class_{}".format(index)))
        value = float(score.get("score", 0.0))
        positive = bool(score.get("positive", False))
        line = "{} {}: {:.3f} {}".format("+" if positive else "-", label, value,
                                         "(positive)" if positive else "")
        color = (0, 130, 0) if positive else (80, 80, 80)
        cv2.putText(canvas, line[:120], (text_x, base_y + 20 * (index + 1)),
                    cv2.FONT_HERSHEY_SIMPLEX, .34, color, 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix.lower(), canvas)
    if not success:
        raise RuntimeError("Could not encode result image")
    encoded.tofile(str(path))


def run(image_path, classifier_path, localizer_path=None, threshold=.5, threads=2,
        overlay_path=None, classifier_size=160, localizer_size=128, classification_thresholds=None):
    if threads < 1:
        raise ValueError("Threads must be a positive integer")
    if not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("Localization threshold must be between 0 and 1")
    cv2.setNumThreads(threads)
    start = time.perf_counter()
    image = read_image(image_path)
    blob = preprocess_center(image, classifier_size)
    net = load_net(classifier_path)
    logits, classification_ms = forward(net, blob)
    classification = classify_logits(logits, classification_thresholds)
    # Release the first network before loading the second on the 512 MB Z2.
    del net, blob
    gc.collect()
    result = dict(
        schema_version=1, image=str(Path(image_path)),
        input_contract=dict(classifier_shape=[1, 3, classifier_size, classifier_size],
                            localizer_shape=[1, 3, localizer_size, localizer_size],
                            layout="NCHW", dtype="float32", color="RGB",
                            normalization="ImageNet mean/std"),
        image_size=dict(width=image.shape[1], height=image.shape[0]),
        runtime=dict(name="opencv_dnn_cpu", opencv=cv2.__version__, threads=threads),
        classification=classification,
        localization=dict(status="not_requested"),
        models=dict(classifier=dict(file=Path(classifier_path).name, sha256=sha256_file(classifier_path))),
        timing_ms=dict(classifier_forward=classification_ms),
        scope_zh="七类模型：睑裂斑、翼状胬肉、结膜下出血、麦粒肿、睑缘炎、泪囊炎、正常。正常类别为研究图像标签；未覆盖其他病种。",
        use_zh="研究演示结果，不能用于确诊、排除疾病或替代眼科检查。",
    )
    if localizer_path is not None:
        indices = [i for i in classification["positive_class_ids"] if i < 2]
        if not indices:
            result["localization"] = dict(status="no_positive_localizable_class", regions=[])
        else:
            blob, geometry = preprocess(image, localizer_size)
            net = load_net(localizer_path)
            logits, localization_ms = forward(net, blob)
            regions = []
            for index in indices:
                mask, region = localize_logits(logits, index, geometry, threshold)
                if overlay_path is not None:
                    path = Path(overlay_path)
                    path = path.with_name(path.stem + "_" + LABELS[index][0] + path.suffix)
                    save_overlay(path, image, mask, region["bbox_xyxy"])
                    region["overlay_file"] = str(path)
                regions.append(region)
            result["localization"] = dict(status="regions_evaluated", regions=regions)
            result["timing_ms"]["localizer_forward"] = localization_ms
            result["models"]["localizer"] = dict(file=Path(localizer_path).name, sha256=sha256_file(localizer_path))
        unsupported = [i for i in classification["positive_class_ids"] if 2 <= i < 6]
        if unsupported:
            result["localization"]["unsupported_classes"] = [
                dict(class_id=i, reason_zh="{}未提供定位标注".format(LABELS[i][1]))
                for i in unsupported]
    if overlay_path is not None:
        save_result_image(overlay_path, image, classification,
                          result.get("localization", {}).get("regions", []))
    result["timing_ms"]["total_including_load_and_io"] = (time.perf_counter() - start) * 1000
    return result


def _save_html_report(path, result, image_path):
    """Load the optional sibling report module in both script and test modes."""
    deploy_dir = str(Path(__file__).resolve().parent)
    if deploy_dir not in sys.path:
        sys.path.insert(0, deploy_dir)
    try:
        from report import save_report
    except ImportError as error:
        raise RuntimeError("HTML report support is missing report.py") from error
    return save_report(path, result, image_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="PYNQ-Z2 CPU inference for seven-class eye-image research models")
    parser.add_argument("--image", required=True)
    parser.add_argument("--classifier", default=str(DEFAULT_MODELS / "mobilenet_v3_small.onnx"))
    parser.add_argument("--localizer", help="Optional Mobile U-Net ONNX; skip to save memory and time")
    parser.add_argument("--threshold", type=float, default=.5, help="Uncalibrated region threshold, default 0.5")
    parser.add_argument("--classification-thresholds", type=float, nargs=len(LABELS),
                        metavar=tuple(name for name, _ in LABELS),
                        help="One classifier threshold per label; overrides metadata, otherwise defaults to the shipped values")
    parser.add_argument("--metadata", help="Optional export JSON containing classification_thresholds or thresholds")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--json", dest="json_path", help="Write UTF-8 JSON to this path")
    parser.add_argument("--overlay", help="Write optional weak-region overlay image")
    parser.add_argument("--report", help="Write an offline HTML report; defaults to <json stem>.html when --json is used")
    args = parser.parse_args(argv)
    try:
        thresholds = args.classification_thresholds
        if args.metadata:
            metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
            if thresholds is None:
                thresholds = metadata.get("classification_thresholds", metadata.get("thresholds"))
                if thresholds is None:
                    raise ValueError("Metadata must contain classification_thresholds or thresholds")
        if thresholds is None:
            thresholds, threshold_source = resolve_thresholds(args.classifier)
        else:
            _, threshold_source = resolve_thresholds(args.classifier, thresholds)
        result = run(args.image, args.classifier, args.localizer, args.threshold, args.threads, args.overlay,
                     classification_thresholds=thresholds)
        result["classification"]["threshold_source"] = threshold_source
        report_path = Path(args.report) if args.report else (
            Path(args.json_path).with_suffix(".html") if args.json_path else None)
        outputs = {}
        if args.overlay:
            outputs["result_image"] = str(Path(args.overlay))
        if report_path is not None:
            outputs["report"] = str(report_path)
        if outputs:
            result["outputs"] = outputs
        if report_path is not None:
            report_image = Path(args.overlay) if args.overlay and Path(args.overlay).is_file() else Path(args.image)
            _save_html_report(report_path, result, report_image)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.json_path:
            path = Path(args.json_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
        print(text)
    except (ValueError, FileNotFoundError, RuntimeError, cv2.error, OSError) as error:
        print("Inference failed: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
