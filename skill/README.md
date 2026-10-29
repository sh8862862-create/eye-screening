# 综合工程技能包

统一入口：[pynq-eye-system/SKILL.md](pynq-eye-system/SKILL.md)。覆盖眼部识别与训练导出、相机与报告、语音、按键、OLED、手势/HID、掌检测 PL、CPU 优化、发布与恢复，共 14 项功能能力，按 8 类工程流程使用。

| 工程流程 | 内容 |
|---|---|
| 版本与完整性 | 当前入口、SHA256、历史归档和恢复 |
| 共享 FPGA | owner、租约、generation、DMA 与恢复 |
| 构建与验证 | HLS、RTL 协仿真、Vivado、ncnn |
| 跟踪与控制排错 | ROI、置信度、丢手重入、HID 门控 |
| 性能研究 | CPU/PL、分阶段测量、并发与统计口径 |
| 外设与业务联调 | 双摄像机、十题问卷、四按键、OLED |
| 模型与结果一致性 | 标签/预处理/阈值、训练导出、报告 |
| 部署与验收回退 | 发布映射、备份、现场验收、迁移 |

## 从工程根目录使用

```sh
python -B skill/pynq-eye-system/scripts/project.py capabilities
python -B skill/pynq-eye-system/scripts/project.py verify
python -B skill/pynq-eye-system/scripts/project.py test
python -B skill/pynq-eye-system/tests/test_project.py
```

命令入口、依赖和输出见 [统一命令](pynq-eye-system/references/commands.md)。技能随配套工程使用，也可独立放置后通过 `--project-root` 指向本版工程。任意 `skill/` 目录不保证宿主自动发现；可以显式要求读取该 `SKILL.md`，或在需要时按宿主的技能安装方式配置。本次仅整理工程文件，不修改全局技能安装。

## 文件和证据

- `pynq-eye-system/scripts/project.py`：统一操作入口，支持能力查询、校验、测试、历史源定位/恢复、推理、报告、构建计划、发布包和状态诊断。
- `pynq-eye-system/references/`：按任务读取的流程、故障分支与验收标准。
- `pynq-eye-system/assets/`：能力清单、部署映射、资源摘要、必要服务配置及实验记录模板。
- `artifact_integrity/verify_manifest.py`：保留原只读校验工具及原调用路径，供工程包校验继续使用。

出处及历史边界见 [证据索引](pynq-eye-system/references/evidence_index.md)。本次完善的说明、摘要与离线验证结果见工程 `docs/preparation.md`。新技能内容没有复制模型或硬件产物；首次 SD 环境安装、训练集获取和实板重新验收仍需相应环境。


本文件的历史工程路径按 [协作版资源与归档说明](../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
