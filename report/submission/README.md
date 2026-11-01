# 自主选题提交材料索引

## GitHub 源码版范围（2026-11-01）

此仓库同步最新提交版的可公开源码、技能、安装构建入口和设计报告。训练数据、现场照片、原始会话、完整历史归档不在公开仓库中。下文涉及这些内容的路径指本地完整提交包；运行资源从 [资源说明](../../docs/resources.md) 导入。已有结果的汇总在 [验证记录](../validation/validation_summary.json)，本次仅同步，不重复硬件与旧工程测试。新 SD 卡完整验收、完整重训练、重新综合实现仍未执行。


日期：2026-11-01。依据用户提供的选题指南截图：3.3.5.1 工程包、3.3.5.2 技能包、3.3.5.4 推荐目录。保持原七类目录和原始历史归档。本轮只补未完成材料，已完成的实验、测试、硬件验收不重复执行。

## 3.3.5.1 工程包逐项对应

| 要求 | 交付入口（相对工程根目录） | 依据与状态 |
|---|---|---|
| 完整工程文件与源码，按实际实现提交 | `src/ps/`、`src/hls/`、`src/native/`、`src/training/`；`data/hardware/ch9329_usb_hid.eprj2` | 当前应用、HLS核、C++桥接、训练源码及PCB工程已提供；本项目采用HLS，不将生成RTL冒充手写RTL |
| 仿真或验证结果，解释功能正确性 | `sim/README.md`、`sim/results/reproduction/validation_summary.json`、`sim/results/submission/reusable_checks.json`、`sim/hls/test_depthwise.cpp` | 原工程上一轮93通过、4项Linux条件跳过；本轮仅新增校验工具行为测试；历史HLS仿真及板端结果保留 |
| 综合实现报告，资源、时钟、关键性能 | `build/reports/palm_output/`、`build/reports/palm_hls_report/`、`build/reports/palm_cosim_report/`；`report/design_report_20261101_v5.docx` | 提供原始报告；PL核100MHz，外设62.5MHz；Routed资源和性能范围见下文，不重跑已有实现 |
| 上板工程、运行脚本、实测与参考对比 | `board/releases/`、`board/config/`、`board/measured_results_index.md`、`report/reproduction/README.md` | bit/hwh、运行库、服务、固件、安装入口、实测/黄金参考索引齐全；原板只读环境检查和用户OLED验收已有证据 |
| 可复现构建脚本 | `build/scripts/build_palm_hls.tcl`、`build_palm_overlay.tcl`、`build_ncnn.sh`、`build_palm_native.sh`、`build_opencv_neon.sh` | 从源码到IP/bit/hwh/ARM库的入口已提供；指定版本/架构与输出拒绝覆盖条件明确，新SD安装和新增构建入口全流程独立复验未执行 |

## 3.3.5.2 技能包逐项对应

| 要求 | 已补材料 | 实际可用性及边界 |
|---|---|---|
| 放在skill，含README说明每项范围、方法、失效条件 | `skill/README.md`，机器可读 `skill/pynq-eye-system/assets/capabilities.json` | 14项能力逐项说明范围/方法/失败/效果/证据，保留一个综合入口 |
| 提示词工作流 | `skill/pynq-eye-system/assets/prompt_workflows.md` | 四类任务模板：软硬件划分、报告分析、共享硬件故障、动作迟滞；整理模板不是原始对话导出 |
| 案例模板 | `skill/pynq-eye-system/assets/case_record.md`、`migration_profile.json`、原 `experiment_record.md` | 新团队可填写环境、契约、黄金参考、触发条件、验证及边界；未填项不默认通过 |
| 校验脚本 | `scripts/compare_results.py`、`scripts/check_implementation.py`、原 `project.py`/`verify_manifest.py` | 新增独立Python数值/资源时序DRC校验器；目录内README给出输入格式、用法、退出码和失败条件 |
| 踩坑清单 | `skill/pynq-eye-system/references/failure_catalog.md` | 对应11类触发现象，含排查步骤、停止条件和项目证据 |
| 可直接复用与来源说明 | `references/reuse_guide.md`、`references/evidence_index.md` | 脱离工程可使用新校验器；迁移硬件参数明确；未声称已有外团队独立成功验证 |

## 3.3.5.4 目录对应

```text
eye_screening/
  README.md
  src/
  sim/
  build/
  board/
  data/
  skill/
    README.md
    pynq-eye-system/
  report/
```

本轮只在已有七类目录内新增内容，不移动已有文件。新增文件名使用英文小写、数字、下划线或连字符；`README.md` 按指南示例、`SKILL.md` 按技能入口固定拼写保留。中文说明仅出现在正文。模型/第三方源码压缩包内部原名保留以保证可构建，不把第三方包内部名称当作项目新命名。

## 已有资源与性能结论，直接引用

| 指标 | 已有值 | 口径与证据 |
|---|---|---|
| LUT / FF / BRAM tile / DSP | 18,332 / 17,339 / 35 / 138 | `build/reports/palm_output/shared_utilization.rpt`，整个共享Overlay |
| Setup WNS / Hold WHS | +0.385ns / +0.026ns | `shared_timing.rpt`，历史Routed实现 |
| DRC | 保留Warning和Advisory原表 | `shared_drc.rpt`，无Error不等于没有告警；不擅自标为全部消除 |
| 掌检测直接forward warm中位数 | 854.4→757.1ms | 保存图同组数据，旧实验；非整机端到端响应 |
| 掌检测RPC warm中位数 | 854.7→777.1ms | RPC自身同组CPU基线；不能把854.4混入该组 |
| TRACK中位数 | 781.0→466.7ms | 保存图TRACK对比；不是现场响应上限 |
| OLED用户实物验收 | 通过 | 2026-11-01重新接线后用户确认“已接好，显示正常” |

具体样本、warm/cold、线程、计时边界和阈值复算见V5设计报告；原始比对数据入口为 `board/measured_results_index.md` 和 `data/reference/README.md`。

## 尚未完成的事项

| 事项 | 当前状态 | 处理方式 |
|---|---|---|
| 实物照片及最终版面中的照片 | 用户明确自行添加 | 保留V5占位，不生成代替实拍的图像 |
| 第二张干净SD卡上的全链路安装/实机验收 | 未执行 | 已有安装流程和环境依赖；需要实际新目标后再记录，不重复检查旧板冒充 |
| 新移植构建入口完整执行、另一工具版本兼容 | 未执行 | 历史实现证据保留，具备环境并需要新构建时再执行 |
| 本次完整重训练及独立测试集效果 | 未执行 | 数据重建和已有检查点导出已经验证；不把它们写成重新训练 |
| 陌生团队跨项目复用效果 | 未实测 | 已提供可独立使用的脚本、模板与迁移步骤，等待真实外部应用记录 |
| 原始聊天全文及公开传播授权范围 | 全文未收集、素材逐项授权仍需确认 | 本轮提供真实选定工作记录与修订文件，不伪造缺失对话，不擅自公开工程 |

上述未完成项明确保留，不作为反复运行既有检查的理由。文件交付可继续提交评审；对“从空白镜像完整复现”或“新工具版本验证通过”的表述应与真实执行范围一致。

## 阅读顺序

1. 根 `README.md` 和 `report/reproduction/README.md`：运行与恢复。
2. V5设计报告：架构、实现和证据范围。
3. `skill/README.md`：技能能力契约和复用方法。
4. `report/submission/collaboration_cases.md`：本轮工作记录与经验来源。
5. `report/reproduction/full_manifest.json`：本版交付清单；压缩包旁SHA256用于版本固定。
