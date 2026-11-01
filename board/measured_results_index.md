# 上板实测与参考结果索引

本页引用既有证据，不代表本轮重新上板。

| 项目 | 实测/参考文件（工程根目录相对路径） | 对比内容与边界 |
|---|---|---|
| 掌检测PL逐层与整网 | sim/archive/workspace/tools/palm_pl_20261028/validation/v6selected_pl_benchmark.json | layer_checks、images；固定输入CPU/PL数值与计时 |
| 掌检测RPC | sim/archive/workspace/tools/palm_pl_20261028/validation/v6selected_rpc_benchmark.json | 同批CPU/RPC；冷启动与warm分开，非摄像头到页面时延 |
| ncnn与跟踪 | sim/archive/workspace/tools/gesture_latency_20261028/validation/pipeline_benchmark.json | 同批TRACK样本；完整统计口径见设计报告 |
| 原生库/环境 | sim/results/reproduction/board_preflight.json | 2026-11-01原板只读检查，不代替新SD验收 |
| OLED | sim/results/reproduction/oled_user_acceptance.json | 用户重新接线后确认显示正常，用户实物验收通过 |

运行入口见 board/README.md、report/reproduction/README.md 和 skill/pynq-eye-system/references/hardware_performance.md。不要以历史结果替代新产物的实测。
