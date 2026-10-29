# 案例证据与版本判定

2026-10-29 整理。来源包括本版压缩工程、项目“语音模块”工作记录及该记录关联的综合技能后续版本。这里是可追溯摘要，不是完整原始对话导出；不包含连接凭据或完整历史命令。

| 案例 | 沉淀的方法 | 本工程证据（相对工程根目录） | 结论边界 |
|---|---|---|---|
| 多外设共用 PL | 单一 owner、共享租约、generation、DMA 生命周期 | `src/ps/shared/shared_fpga.py`、`src/ps/palm/pl_backend.py`、`report/palm_pl_20261028.md` | 其他板卡地址和缓存一致性需重验 |
| 伸直正常、弯曲漏识别 | 新帧关键点验证、保留真实 ROI 角点、一次有界重试 | `report/gesture_reliability_20261028.md`、`sim/gesture/test_tracking.py` | 历史同批回放 10/12→12/12；非整体准确率 |
| 模型变快仍有延迟 | 分开测算子、整网、IPC、采集、控制等待及主机输出 | `report/gesture_latency_20261028.md`、`report/current_release.md` | 保存图片约 458–467 ms，不是现场延迟保证 |
| 只取消关键点显示 | 绘制与内部姿态推理解耦 | `report/palm_pl_20261028.md`、`src/ps/camera_web/workers.py` | 内部关键点仍用于控制 |
| 停止后自动恢复风险 | BTN2 与网页配套，进程及设备占用联合验证 | `report/handoff_20261029.md`、`src/ps/shared/button_service.py`、`src/ps/camera_web/service.py` | 页面在线不代表检查任务仍运行 |
| 多批次发布混装 | 当前源清单逐文件选取，不能整目录覆盖 | `report/organization/current_sources.json`、`board/README.md` | 离线文件校验不证明板端同版 |
| 正常阈值调整 | 标签、权重、阈值、判定优先级一起追溯 | `data/models/eye/current_operating_threshold.json` | 0.85 为当前操作配置，不代表重新完成独立评估 |

项目工作记录中，2026-10-29 用户明确反馈“弯曲和伸直均正常”。这是一条历史真人姿态验收；当前技能整理没有重新做真人测试。该记录还提出整合全部项目能力、采用研究者视角编写文档，本版保留统一技能入口。

早期交接正文记载“CPU 推理 + FPGA 外设”、OLED failed；其后追加了六层掌检测 PL、ncnn 优化及服务状态变化。读取交接时必须结合追加章节、当前版本说明和实际文件。较新的服务 active 记录仍不能代替 OLED 实屏验收。

## 兼容性来源

本版以用户上传的 `eye_screening.zip` 为数据和生产源码基线，吸收另一份项目目录中的 `skill/pynq-eye-system/` 操作入口和资料。导入核对发现恢复脚本、版本说明、当前源清单和包校验脚本四处摘要与后续工程版不同。本版保留上传工程的原始基线，只对包校验脚本增加标准技能文件名兼容；配套技能资源清单据此记录本版摘要。原始归档清单和当前源码清单不重写。

具体导入来源摘要、最终变更和测试记录位于工程 `docs/preparation.md`。任何后续修改应追加自己的验证结果，不将本次离线检查表述为新硬件验收。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
