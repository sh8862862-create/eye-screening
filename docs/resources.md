# 外部资源和完整归档

## 源码版可直接做什么

普通 clone 只有源码、文档、元数据和数值回归样本，可以运行全部默认离线测试。资源不存在时 `status` 和 `capabilities` 明确列出缺项；这不是推理或上板已就绪。`verify` 不把已登记的可选资源缺失当成源码损坏，但发现已存在的资源摘要错误仍失败。

## 历史版本资源（仅对应旧提交）

文件名为 `eye-screening-resources-20261029.zip`，由维护者随对应私有 Release 附件或获授权的本地传输提供。可在[历史资源发布页](https://github.com/sh8862862-create/eye-screening/releases/tag/v0.1.0-collaboration)下载。包含 15 个模型、硬件、运行库、ncnn 源码包及问卷源表，路径与 SHA256 固定在 [资源清单](../resources/manifest.json)。不含训练集或现场照片。

获得文件后，在仓库根目录运行：

```sh
python -B tools/resources.py import --archive /path/eye-screening-resources-20261029.zip
python -B tools/resources.py verify
```

导入先核整个压缩包摘要，再核精确成员列表、路径、长度及每个文件摘要，随后写入项目规定位置。不同版本、额外文件和修改过的本地资源会被拒绝；不覆盖未知版本。相同内容重复导入是幂等操作。磁盘写入失败会删除本轮已创建文件，原有文件保留。

仓库当前公开，可在 Releases 页面下载公开附件；不要在文档或命令中写 token。未来可改变传输方式，但应先校验仓库内清单，不直接信任外部 ZIP 自带清单。

## 历史操作

完整归档继续由原工程持有。要查询旧训练/部署脚本、恢复原布局或检查全包，在 PowerShell 设置：

```powershell
$env:EYE_FULL_PACKAGE_ROOT = '你的完整工程根目录'
python -B skill/pynq-eye-system/scripts/project.py locate model_retrain/train_normal7.py
python -B skill/pynq-eye-system/scripts/project.py restore --destination C:/dev/restored-eye-workspace
python -B sim/verify_package.py --full-package-root $env:EYE_FULL_PACKAGE_ROOT
```

完整包应有原来的 report/organization/file_inventory.json 和 sim/verify_package.py。恢复目录必须是新的工程外目录。归档尚未取得时命令明确失败，不将缺失历史测试计为通过。仅有训练脚本不能代替完整数据来源、检查点和训练环境。

`sim/verify_full_package.py` 保留原完整包校验器快照，作为参考；请对实际完整包调用上一条命令，使其自身脚本和清单保持配套。

## 2026-11-01 配套资源更新

当前 main 使用 `eye-screening-resources-20261101.zip`，从 [v0.2.0-submission](https://github.com/sh8862862-create/eye-screening/releases/tag/v0.2.0-submission) 获取。包含原 15 项资源及 OpenCV ARMv7 快照、对应源码、两个字体与 Waitress wheel，摘要以 resources/manifest.json 为准。许可证分别位于 data/runtime/、data/runtime/fonts/、data/third_party/。旧发布保留供旧提交使用，不把旧资源 ZIP 导入新版 main。

```sh
python -B tools/resources.py import --archive /path/eye-screening-resources-20261101.zip
python -B tools/resources.py status
```

本仓库当前为公开仓库；正文前部“私有 Release”描述为最初建仓方案，不代表当前访问权限。公开附件不包含训练数据、现场照片或完整历史会话。

## 独立图像训练集

`training set/` 随仓库提供，共 2053 张图像，含英文类别目录、labels.csv 和 summary.json。它不放入运行资源 ZIP，也不改变 resources/manifest.json 的运行资源版本；详细来源、多标签与划分见 [训练集说明](../training%20set/README.md)。
