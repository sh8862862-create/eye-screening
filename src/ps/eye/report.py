#!/usr/bin/env python3
"""Self-contained Chinese inference reports. Python >= 3.6, standard library only."""

import argparse
import base64
from datetime import datetime
import html
import json
import math
from pathlib import Path
import sys


LABELS_ZH = {0: "睑裂斑", 1: "翼状胬肉", 2: "结膜下出血",
             3: "麦粒肿", 4: "睑缘炎", 5: "泪囊炎", 6: "正常"}
LOCALIZATION_STATUS = {
    "not_requested": "本次未运行定位模型。",
    "no_positive_localizable_class": "本次没有超过分类阈值且支持定位的类别，未运行定位推理。",
    "regions_evaluated": "已对超过分类阈值且支持定位的类别完成区域评估。",
    "region_found": "存在超过定位阈值的区域",
    "no_region_above_threshold": "没有区域超过定位阈值",
    "unsupported_class": "当前类别不支持定位",
}
STYLE = """
:root { color-scheme: light; --ink: #202927; --muted: #59635f; --line: #d9dfdb;
  --green: #17624c; --red: #a93737; --soft: #f3f6f4; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink); font: 15px/1.65 "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; letter-spacing: 0; }
main { width: min(1080px, 100%); margin: 0 auto; padding: 32px 28px; }
header { border-top: 5px solid var(--green); border-bottom: 1px solid var(--line); padding: 20px 0; }
h1 { margin: 0 0 6px; font-size: 28px; line-height: 1.35; }
h2 { font-size: 19px; line-height: 1.4; margin: 0 0 14px; }
h3 { font-size: 16px; margin: 0 0 8px; }
p { margin: 6px 0 12px; }
.muted, figcaption { color: var(--muted); font-size: 13px; }
.notice { border-left: 3px solid var(--red); padding: 8px 14px; margin: 20px 0; background: #fcf5f5; }
.summary { padding: 18px 0 20px; border-bottom: 1px solid var(--line); }
.summary-label { color: var(--muted); font-size: 13px; margin: 0 0 5px; }
.summary-text { font-size: 23px; font-weight: 700; margin: 0; color: var(--green); overflow-wrap: anywhere; }
.summary-text.uncertain { color: var(--muted); }
.primary { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr); gap: 28px; padding: 26px 0; border-bottom: 1px solid var(--line); }
section { min-width: 0; }
.band { padding: 24px 0; border-bottom: 1px solid var(--line); }
figure { margin: 0; min-width: 0; }
img { display: block; width: 100%; max-height: 380px; object-fit: contain; background: var(--soft); }
figcaption { padding-top: 8px; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; font-size: 14px; table-layout: fixed; }
th { background: var(--soft); color: var(--muted); text-align: left; font-weight: 500; }
th, td { border-bottom: 1px solid var(--line); padding: 10px 8px; vertical-align: top; overflow-wrap: anywhere; }
.scores th:first-child { width: 34%; }
.score { font-variant-numeric: tabular-nums; }
.positive { color: var(--red); font-weight: 700; }
.negative { color: var(--muted); }
.normal { color: var(--green); font-weight: 700; }
.facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
dl { margin: 0; }
dt { color: var(--muted); font-size: 13px; }
dd { margin: 4px 0 14px; overflow-wrap: anywhere; }
.gallery { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(280px, 100%), 1fr)); gap: 24px; margin-top: 18px; }
.region { min-width: 0; }
code { font-family: Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }
footer { padding: 20px 0 0; font-size: 13px; color: var(--muted); }
@media (max-width: 760px) {
  main { padding: 18px 16px; } h1 { font-size: 24px; }
  .primary { grid-template-columns: minmax(0, 1fr); gap: 24px; }
  .facts { grid-template-columns: minmax(0, 1fr); gap: 0; }
  th, td { padding: 8px 5px; font-size: 13px; }
}
@media print {
  @page { size: A4; margin: 14mm; }
  main { width: 100%; padding: 0; }
  body { font-size: 11px; } h1 { font-size: 22px; } h2 { font-size: 16px; }
  .primary { gap: 18px; } img { max-height: 250px; }
  figure, .region, .notice, tr { break-inside: avoid; }
  header, h2, h3 { break-after: avoid; }
}
"""


def _escape(value):
    return html.escape(str(value), quote=True)


def _number(value, places=4):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未记录"
    return ("{:." + str(places) + "f}").format(number) if math.isfinite(number) else "未记录"


def _image_uri(path):
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data.startswith(b"BM"):
        mime = "image/bmp"
    else:
        raise ValueError("Report images must be PNG, JPEG, GIF, WebP, or BMP: {}".format(path))
    return "data:{};base64,{}".format(mime, base64.b64encode(data).decode("ascii"))


def _region_html(region):
    label = region.get("label_zh", LABELS_ZH.get(region.get("class_id"), region.get("label", "定位区域")))
    status = region.get("status", "未记录")
    content = '<div class="region"><h3>{}</h3><p>{}</p>'.format(
        _escape(label), _escape(LOCALIZATION_STATUS.get(status, status)))
    overlay = region.get("overlay_file")
    if overlay:
        try:
            content += '<figure><img src="{}" alt="{}的定位叠加图"><figcaption>{}</figcaption></figure>'.format(
                _image_uri(overlay), _escape(label), _escape(overlay))
        except (OSError, ValueError):
            content += '<p class="muted">定位叠加图未能读取：{}</p>'.format(_escape(overlay))
    else:
        content += '<p class="muted">本次未保存该类别的定位叠加图。</p>'
    if region.get("bbox_xyxy") is not None:
        content += '<p class="muted">区域坐标（左上 / 右下，不含右下边界）：{}</p>'.format(
            _escape(region["bbox_xyxy"]))
    if region.get("area_fraction") is not None:
        content += '<p class="muted">区域占图像比例：{}；定位阈值：{}</p>'.format(
            _number(region.get("area_fraction")), _number(region.get("threshold")))
    content += '<p class="muted">{}</p></div>'.format(_escape(region.get(
        "note_zh", "区域由矩形框弱监督学习得到，不代表精确病灶轮廓或医学分割。")))
    return content


def save_report(path, result, image_path):
    """Write one portable HTML file, embedding original and saved region images.

    Region overlay paths follow infer.py's process-working-directory convention.
    Missing overlays are noted explicitly; a missing original image is an error.
    """
    classification = result.get("classification", {})
    scores = classification.get("scores", [])
    positive_names = [item.get("label_zh", item.get("label", "未命名类别"))
                      for item in scores if item.get("positive")]
    if not scores:
        positive_names = classification.get("positive_labels_zh", classification.get("positive_labels", []))
    summary = "、".join(str(label) for label in positive_names) if positive_names else "未确定：没有类别达到判定条件"
    rows = []
    for item in scores:
        positive = bool(item.get("positive"))
        rows.append('<tr><td>{}</td><td class="score">{}</td><td class="score">{}</td><td class="{}">{}</td></tr>'.format(
            _escape(item.get("label_zh", item.get("label", "未命名类别"))),
            _number(item.get("score"), 6), _number(item.get("threshold"), 2),
            "normal" if item.get("label") == "normal" and positive else "positive" if positive else "negative",
            ("正常" if positive else "未判正常") if item.get("label") == "normal" else "超过阈值" if positive else "未超过"))
    if not rows:
        rows.append('<tr><td colspan="4">此结果未记录逐类别分数。</td></tr>')
    localization = result.get("localization", {})
    status = localization.get("status", "未记录定位状态")
    localization_html = '<p>{}</p>'.format(_escape(LOCALIZATION_STATUS.get(status, status)))
    for unsupported in localization.get("unsupported_classes", []):
        label = LABELS_ZH.get(unsupported.get("class_id"), unsupported.get("label_zh", "该类别"))
        localization_html += '<p>{}：{}</p>'.format(_escape(label), _escape(unsupported.get(
            "reason_zh", "当前模型不支持该类别定位。")))
    if localization.get("regions"):
        localization_html += '<div class="gallery">{}</div>'.format(
            "".join(_region_html(region) for region in localization["regions"]))
    runtime = result.get("runtime", {})
    timing = result.get("timing_ms", {})
    image_size = result.get("image_size", {})
    timing_html = "".join('<dt>{}</dt><dd>{} ms</dd>'.format(label, _number(timing[key], 2))
                          for key, label in (("classifier_forward", "分类模型前向耗时"),
                                             ("localizer_forward", "定位模型前向耗时"),
                                             ("total_including_load_and_io", "推理总耗时（含加载和读写）"))
                          if key in timing)
    models_html = "".join('<tr><td>{}</td><td>{}</td><td><code>{}</code></td></tr>'.format(
        _escape({"classifier": "分类模型", "localizer": "定位模型"}.get(name, name)),
        _escape(model.get("file", "未记录")), _escape(model.get("sha256", "未记录")))
        for name, model in result.get("models", {}).items())
    source = result.get("image", str(image_path))
    result_image_ref = result.get("outputs", {}).get("result_image")
    saved_result_image = bool(result_image_ref and Path(image_path).name == Path(result_image_ref).name)
    image_heading = "结果图" if saved_result_image else "输入图像"
    image_alt = "本次推理的结果图" if saved_result_image else "本次推理的原始眼部图像"
    document = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>眼部图像研究推理报告</title><style>{style}</style></head><body><main>
<header><h1>眼部图像研究推理报告</h1><p class="muted">报告生成时间：{generated} · 图像：{source}</p></header>
<div class="notice">研究演示结果，不能用于确诊、排除疾病或替代眼科检查。模型分数未经概率校准，不代表患病概率。</div>
<section class="summary"><p class="summary-label">模型分类结果</p><p class="summary-text {summary_class}">{summary}</p></section>
<div class="primary"><section><h2>{image_heading}</h2><figure><img src="{original}" alt="{image_alt}"><figcaption>{source} · {width} × {height} 像素</figcaption></figure></section>
<section><h2>分类分数</h2><table class="scores"><thead><tr><th>类别</th><th>模型分数</th><th>阈值</th><th>判定</th></tr></thead><tbody>{rows}</tbody></table>
<p class="muted">六种眼病可同时检出；正常需正常分数达到阈值且六种眼病均未达阈值。全部低于阈值显示未确定。</p>
<p class="muted">{classification_note}</p></section></div>
<section class="band"><h2>定位结果</h2>{localization}</section>
<section class="band"><h2>运行信息</h2><div class="facts"><dl><dt>运行方式</dt><dd>{runtime}</dd><dt>OpenCV 版本 / 线程数</dt><dd>{opencv} / {threads}</dd></dl>
<dl>{timing}</dl><dl><dt>分类阈值来源</dt><dd>{threshold_source}</dd><dt>结果格式版本</dt><dd>{schema}</dd></dl></div></section>
<section class="band"><h2>模型记录</h2><table><thead><tr><th>模型用途</th><th>模型文件</th><th>SHA-256</th></tr></thead><tbody>{models}</tbody></table></section>
<footer><p>{scope}</p><p>{use}</p></footer>
</main></body></html>
""".format(style=STYLE, generated=_escape(datetime.now().astimezone().isoformat(timespec="seconds")),
           source=_escape(source), summary_class="" if positive_names else "uncertain", summary=_escape(summary),
           image_heading=_escape(image_heading), image_alt=_escape(image_alt),
           original=_image_uri(image_path), width=_escape(image_size.get("width", "未记录")),
           height=_escape(image_size.get("height", "未记录")), rows="".join(rows),
           classification_note=_escape(classification.get("note_zh", "")), localization=localization_html,
           runtime=_escape("OpenCV DNN / CPU（未使用 FPGA 加速）" if runtime.get("name") == "opencv_dnn_cpu"
                           else runtime.get("name", "未记录")),
           opencv=_escape(runtime.get("opencv", "未记录")), threads=_escape(runtime.get("threads", "未记录")),
           timing=timing_html or "<dt>运行耗时</dt><dd>未记录</dd>",
           threshold_source=_escape(classification.get("threshold_source", "未记录")),
           schema=_escape(result.get("schema_version", "未记录")), models=models_html,
           scope=_escape(result.get("scope_zh", "本报告展示模型实际输出类别；正常为模型判定，不能排除其他眼病。")),
           use=_escape(result.get("use_zh", "研究演示结果，不能用于确诊、排除疾病或替代眼科检查。")))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


def _resolve_asset(path, result_dir):
    path = Path(path)
    return result_dir / path if not path.is_absolute() and not path.is_file() else path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create an offline Chinese HTML report from an existing inference JSON")
    parser.add_argument("--result", required=True, help="Existing inference JSON")
    parser.add_argument("--image", required=True, help="Original input image")
    parser.add_argument("--html", required=True, help="Output self-contained HTML file")
    args = parser.parse_args(argv)
    try:
        result_path = Path(args.result)
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError("Inference JSON must contain an object")
        for region in result.get("localization", {}).get("regions", []):
            if region.get("overlay_file"):
                region["overlay_file"] = str(_resolve_asset(region["overlay_file"], result_path.parent))
        output = save_report(args.html, result, _resolve_asset(args.image, result_path.parent))
        print("Report saved: {}".format(output))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print("Report failed: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
