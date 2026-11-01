# 综合工程技能包

本技能包覆盖项目全部14项能力，按8类工程流程使用。统一入口：[SKILL.md](pynq-eye-system/SKILL.md)。本版针对赛道3.3.5.2补齐逐项适用范围、用法、失败条件、验证效果和经验来源；没有重复执行已完成的工程验收。

## 陌生团队从这里开始

- [直接复用指南](pynq-eye-system/references/reuse_guide.md)：最小依赖、迁移参数、命令、退出码、停止条件及验证边界。
- [提示词工作流](pynq-eye-system/assets/prompt_workflows.md)：软硬件划分、实现报告诊断、共享硬件故障、动作迟滞四类模板。
- [迁移参数模板](pynq-eye-system/assets/migration_profile.json)与[案例记录模板](pynq-eye-system/assets/case_record.md)：用于新项目，未填项不代表通过。
- [数值黄金参考校验器](pynq-eye-system/scripts/compare_results.py)与[资源/时序/DRC校验器](pynq-eye-system/scripts/check_implementation.py)：只依赖Python标准库，可脱离本项目运行。
- [踩坑清单](pynq-eye-system/references/failure_catalog.md)：触发条件、排查顺序、停止条件及对应历史证据。

## 每项能力的使用契约

下表中的路径相对工程根目录；`project.py` 位于 `skill/pynq-eye-system/scripts/`。单独使用技能时传 `--project-root` 或设置 `EYE_PROJECT_ROOT`。标准技能文件名SKILL.md和指南示例README.md保留固定拼写，其余新增文件名使用小写英文、数字、下划线或连字符。

| 能力 | 适用范围 | 使用方法 | 失败条件 | 已验证效果及证据 |
|---|---|---|---|---|
| [系统状态与版本审计](pynq-eye-system/references/system_contract.md) | 版本或环境需要追溯 | project.py capabilities；snapshot --url <地址> 只读采集；无变更时引用既有清单 | 地址不可达、文件摘要不符、boot/generation 不明时不推断板端状态 | sim/results/reproduction/board_preflight.json；现有板环境只读检查通过 |
| [双摄像机与网页工作流](pynq-eye-system/references/camera_workflow.md) | 双相机顺序采集、照片确认、无眼拦截 | 读取 camera_workflow；新目标先 configure_cameras.py；使用 sim/run_camera_tests.py 和 sim/test_eye_presence.py 验证改动 | 角色未确认、图片哈希变化、采集未释放、objdetect/XML 缺失时禁止进入分析 | 相机31项中27通过、4项Linux跳过；眼睛检测18项通过，见 offline_tests.log |
| [四按键与停止确认流程](pynq-eye-system/references/camera_workflow.md) | BTN0到BTN3与业务状态关联 | 按 camera_workflow 状态表核对 button_service；现场验收矩阵逐项记录 | 无待确认照片时BTN3不分析；BTN2之后禁止网页续连自动恢复 | 用户确认BTN3进入分析、BTN2关闭且不恢复；report/ai_collaboration.md |
| [语音固件问卷和答案采集](pynq-eye-system/references/voice_buttons.md) | WonderEcho问卷播报与答案读取 | 读取 voice_buttons；核对5V、协议表、wonderecho及固件；诊断入口 voice_check.py | 无唤醒回应、供电不符、I2C NACK、协议不匹配时先定位硬件和协议 | report/voice_handoff_20261025.md；用户确认连续提问 |
| [七类眼部推理与定位](pynq-eye-system/references/eye_models.md) | 固定眼部图像七类分类和两类弱定位 | project.py infer --image <图像> --output-dir <新目录> --localize；无眼检测属于网页前置门控 | 输入不支持、模型/元数据不同版、预处理错用时停止；不能由分类置信度证明眼睛存在 | sim/results/reproduction/export_validation.json；6样例导出误差4.0054e-05；不代表临床性能 |
| [数据治理重训练导出和阈值](pynq-eye-system/references/eye_models.md) | 复建数据、训练候选及导出 | build/scripts/train_normal7.py prepare/train/export；指定独立输出目录 | 数据缺失、输出已存在、标签/划分不一致、许可范围不清时不覆盖/分发 | dataset_rebuild.json四数组相等；本轮未重训12个epoch，导出用已有检查点 |
| [HTML 报告与结果追溯](pynq-eye-system/references/eye_models.md) | 照片、模型结果、报告及OLED一致追溯 | project.py report 或 infer；按 image_sha256、检查号、结果与报告摘要核对 | 不同检查号、照片或报告被改动时阻止阅片/HID | src/ps/camera_web/service.py verify_report；样例生成结果已在上一轮通过 |
| [OLED 状态显示与有限恢复](pynq-eye-system/references/display_peripherals.md) | 128×64状态及结果显示 | 读取 display_peripherals；先查线/字体/共享I2C，再看显示状态 | 无ACK、字体缺失或generation不一致时不持续错误写入 | oled_user_acceptance.json；2026-11-01用户重新接线后确认显示正常 |
| [捏合食指滚动与可靠跟踪](pynq-eye-system/references/gesture_hid.md) | 捏合解锁、食指弯曲/伸直、离手重入 | 读取 gesture_hid；固定帧回放和 sim/run_gesture_tests.py；只对新改动重跑 | 无新姿态、低置信度、多手、旧帧时停止运动输出；保持交互模式与运动基线分离 | 29项既有测试通过；用户确认弯曲伸直正常和重入无跳动 |
| [CH9329 串口与 USB 控制](pynq-eye-system/references/gesture_hid.md) | 报告就绪后的CH9329 PMODA控制 | 核对9600/8N1/62.5MHz与report门控；诊断先读状态，明确授权后才发送 | 报告未就绪、过期seq、租约失效、USB未接主机时不连续重发 | report/handoff_20261029.md及手势验收记录；不推断任意USB主机兼容 |
| [掌检测 PL 构建和黄金参考](pynq-eye-system/references/hardware_performance.md) | HLS深度卷积与联合Overlay | project.py build hls/overlay 默认计划；新实现用check_implementation.py与compare_results.py | 工具/IP缺失、时序负裕量、严重DRC、数值超差、DMA未idle时停在相应阶段 | build/reports/palm_output；历史WNS0.385ns、WHS0.026ns；本轮不重综合 |
| [ncnn 裁剪与延迟验证](pynq-eye-system/references/hardware_performance.md) | ncnn/裁剪与分阶段延迟 | 读取 hardware_performance；build ncnn；固定同组warm/cold输入分开统计 | 预览帧率代替推理、不同组基准相除、跨时钟直接相减时结论无效 | report/gesture_latency_20261028.md；TRACK 781→466.7ms为保存图口径 |
| [一致发布备份与回退](pynq-eye-system/references/deployment_recovery.md) | 新目标安装或已有板一致升级 | 新目标 install_fresh.py；已有板按deployment_recovery备份；release只生成载荷 | 未知版本、不同目标文件、活跃会话、无备份、DMA未idle时拒绝安装 | test_fresh_install.py 5项通过；现有板未写入；新SD整机验收仍待做 |
| [完整闭环验收与工程迁移](pynq-eye-system/references/acceptance.md) | 按功能闭环交付及团队迁移 | 复现README验收矩阵；migration_profile和case_record填写迁移差异；新增脚本结果单独记录 | 只凭文件校验/服务active/模板占位不能判为实物验收 | validation_summary.json复用上一轮93通过、4跳过；外团队迁移未实测 |

## 验证与复用边界

已有工程结果见 [上一轮验证记录](../report/validation/validation_summary.json)，本次不重跑。新增两个校验器仅通过 `pynq-eye-system/tests/test_reusable_checks.py` 的合成输入行为测试；新结果见 [新增技能检查](../report/validation/reusable_checks.json)。

典型历史失败/观察与技能的关系见 [证据索引](pynq-eye-system/references/evidence_index.md)及踩坑清单。这里没有把陌生团队尚未执行的迁移写成成功，也没有将提示词模板描述为已验证的通用稳定成功率。

完整工程恢复见 [复现说明](../report/reproduction/README.md)；最新提交对应关系见 [提交索引](../report/submission/README.md)。技能是工程经验及操作入口，不包含独立的SD镜像。执行涉及真实硬件的操作前按当前任务授权及运行状态决定；读取技能本身不会部署板卡。

## 公开源码与完整工程的区别

本页历史结果来自完整提交包；公开仓库已增加 `training set/` 七类图像训练集；现场检查照片、训练缓存与检查点、历史归档仍单独管理。缺失的归档资源列入 capabilities 的 archive_resources，不能标为运行就绪。运行资源需按 [资源说明](../docs/resources.md) 导入；来源与本次同步范围见 [同步记录](../report/github_sync_20261101.md)。
