# 眼部模型 训练与报告

## 当前推理

七类顺序固定为睑裂斑、翼状胬肉、结膜下出血、麦粒肿、睑缘炎、泪囊炎、正常。分类网络为 MobileNetV3-Small，定位网络为 Mobile U-Net；模型和元数据在 `data/models/eye/`。显式阈值来自元数据，当前为 `[0.80,0.55,0.55,0.50,0.50,0.50,0.85]`，不能复用历史默认 0.98 或 .99。

```sh
python skill/pynq-eye-system/scripts/project.py infer --image /path/eye.jpg --output-dir /new/inspection
python skill/pynq-eye-system/scripts/project.py infer --image /path/eye.jpg --output-dir /new/localized --localize
```

验证输出的类别顺序、阈值来源、模型哈希和时间字段。六类病变可同时超过阈值；正常需正常分数达阈值且六类病变均未达阈值。所有分数不足时保留未确定。无定位标签的类别不能凭空生成病灶位置。报告定位属于原模型定义的弱定位，不扩展为已验证的临床诊断。

## 数据与重训练

通过 `restore --destination /new/workspace` 还原 `model_retrain/`，先阅读 `train_normal7.py`、`finetune_board_all6.py`、`export_normal7.py`、`normal7/current_operating_threshold.json`。完整训练数据不在技能包中；原脚本依赖 `dataset6.npz`、正常图像和 sources.csv、六类检查点。必须先核对路径、来源许可、标签和数据划分，不能仅因脚本存在就启动训练。

现有流程使用像素摘要与感知哈希识别近重复，保留既有病变数据的 train/val/test 划分，避免新增正常图像泄漏至测试集。运行前输出数据清单与排除理由，固定随机种子，使用独立验证集选择模型和阈值。

导出 ONNX 后核对输入布局、归一化、输出顺序、激活函数与 OpenCV 4.5.4 兼容性；同一批输入比较训练框架、主机与板端数值和特征。记录每类 Precision/Recall/F1、混淆情况、宏平均及阈值来源。增加样本数不自动代表准确率改善。

部署之前保存当前权重、配置、推理/报告代码与模型摘要，使用 deployment_recovery.md 的原子版本流程；不得覆盖历史检查结果。当前 `infer` 和 `report` 两个入口可在无板卡环境复现模型输出与 HTML 生成，训练和板端性能需各自环境。

## 更新前后的一致性检查

输入：候选权重、对应元数据、类别顺序、预处理实现、固定样本及原版输出。先核对 RGB、NCHW、输入 160、ImageNet mean/std 和独立 sigmoid，不能误换 softmax。预处理以 `infer.py` 的 letterbox、插值与填充值实现为准。

执行 `infer --help` 及底层 `src/ps/eye/infer.py --help` 确认实际参数。底层脚本通过 `--classifier`、`--metadata` 和可选 `--localizer` 指定模型；不使用文档中可能残留的 `--models` 写法。

判定回归至少覆盖正常分数在 0.85 两侧、病变与正常同时超阈值、所有类别未达阈值、多病变同时阳性、输入异常；按实际实现核对恰好等于阈值时的 `>=` 行为。阈值示例来自当前配置，修改阈值应有新版本和评测依据。

同一检查比较结果 JSON、HTML、网页及 OLED 中的检查号、最终决策和类别；检查报告引用的是本次图像及本版权重。问卷答案的保存和模型推理是不同环节，不能未经设计把问卷回答当成模型输入。

候选模型数值、配置或输出不匹配时停止部署，恢复权重与配置整套基线，保留失败样本。完成条件为前后差异可解释、必要数值/业务回归通过、报告可追溯；没有外部训练数据或板端环境时分别标注未重训练/未测板端。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
