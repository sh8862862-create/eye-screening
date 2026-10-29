---
name: pynq-eye-system
description: 开发、复现、排查和维护 PYNQ-Z2 眼表筛查与手势交互工程，覆盖七类模型、双摄像机、语音、按键、OLED、HID、共享 FPGA、HLS 构建、性能测量及部署回退。用于此工程及明确要求迁移其接口的任务。
---

# PYNQ-Z2 眼表筛查与手势交互综合技能

以实际研究流程为工作单元：版本与硬件 → 十题问卷 → 眼部采集 → 确认分析 → 报告/OLED → 手势阅片。按本次任务选择下面的流程，仅读取相关参考资料。14 项功能能力归并到 8 类工程流程，共用一个入口。

## 源码协作版优先规则

本版默认 `verify` 检查可编辑源码仓库，允许已登记的外部资源尚未导入；缺失项在 `capabilities` 中明确显示。推理、ncnn 构建和发布前必须按 [资源与归档](../../docs/resources.md) 导入对应资源。历史 locate/restore 需要 `EYE_FULL_PACKAGE_ROOT`，不从协作目录猜测不存在的归档。

当前协作源码由 Git 提交确定；`report/organization/current_sources.json` 是此次准备时的出处快照，不是日常编辑锁。发布仍核对选定源码摘要。完整归档验证用 `sim/verify_package.py --full-package-root` 显式执行。文档中的原 report/organization/file_inventory.json 指完整包，不是本仓库必需文件。

## 定位与基线

1. 定位配套工程根目录。随工程使用时 `scripts/project.py` 自动定位；单独放置时，在子命令前传入 `--project-root`，或设置 `EYE_PROJECT_ROOT`。命令示例见 [统一命令](references/commands.md)。技能没有内置模型、硬件工具或整张 SD 镜像。
2. 阅读工程 `report/current_release.md`、`report/organization/current_sources.json` 和任务涉及的源文件。日期相近的发布目录不是完整的最终快照：worker、控制器和 tracker 的最终版本分属不同修订。
3. 执行 `capabilities` 确认资源，执行 `verify` 校验基线。基线不符时先查明差异，不通过重写清单将未知文件视为可信。
4. 历史交接正文、后续追加记录和当前源码若有差异，用具体文件摘要和最新相关证据定位版本。板端实时状态必须另行观测；本地文档不能证明设备当前在线。

## 八类流程

| 任务 | 入口 | 预期交付 |
|---|---|---|
| 文件缺失、复制迁移、版本混装、来源恢复 | [版本与完整性](references/artifact_integrity.md) | 校验结果、最终文件选择、差异与出处 |
| Overlay 冲突、租约失效、DMA 超时 | [共享 FPGA](references/shared_fpga.md) | owner/generation 证据、问题层次、恢复结果 |
| 掌检测核修改、硬件重建、ncnn 库构建 | [构建与验证](references/build_validation.md) | 工具版本、仿真/实现报告、配套产物 |
| 弯曲漏识别、丢手、方向错误、有识别无滚动 | [跟踪与 HID](references/gesture_hid.md) | 分阶段定位、回放与控制回归、现场验收 |
| CPU/PL 选择、卡顿、帧率与延迟测量 | [性能研究](references/hardware_performance.md) | 原始样本、测量条件、分项及端到端结果 |
| 双相机、问卷、按键、OLED 的完整联调 | [相机与业务](references/camera_workflow.md)、[语音](references/voice_buttons.md)、[显示](references/display_peripherals.md) | 状态迁移、外设观测、停止与恢复验证 |
| 七类模型训练导出、阈值更新、报告一致性 | [模型与报告](references/eye_models.md) | 配套模型配置、数值回归、结果追溯 |
| 发布、回退、完整验收、跨板迁移 | [部署与恢复](references/deployment_recovery.md)、[验收矩阵](references/acceptance.md) | 备份清单、版本比对、验收与未完成项 |

接线、地址及服务名按需阅读 [系统契约](references/system_contract.md)。历史案例和结论边界见 [证据索引](references/evidence_index.md)。`assets/capabilities.json` 是功能索引；`deployment_map.json` 是发布映射；`resource_manifest.json` 是本版配套资源摘要。

## 实施约束

- 共享 FPGA 只有 owner 下载配置。消费者持租约并验证 generation；DMA 未 idle 时保留缓冲区和租约，不通过强杀消费者抢占硬件。
- bit/hwh 同批，地址和运行库匹配。掌检测六个深度卷积节点在 PL，关键点网络在 CPU ncnn FP32；隐藏关键点绘制不取消内部关键点推理。
- BTN2 停止采集与相关任务，保持关闭且不因网页续连恢复；BTN3 仅对当前待确认图像触发分析。
- 无本帧有效姿态时不重复旧 HID 动作；离手保持解锁、返回首帧静默。食指伸直上滚、弯曲下滚。
- 已有任务授权仅用于相应工作。整理技能、读取日志或生成发布包不触发设备部署；发布命令生成的是待安装载荷。
- 记录采用研究目标、方法、证据和局限的表述。离线回放、单元测试、服务观测和实物反馈分别记录，不扩展历史结论。

## 完成与记录

按任务报告变更文件、输入和版本、运行命令、实际结果、部署状态、回退定位及未验证内容。可使用 [实验记录模板](assets/experiment_record.md)；模板中的待填写项不代表通过。环境缺失时完成可运行部分，保存明确的阻塞原因。技能测试入口见 [验收矩阵](references/acceptance.md)。
