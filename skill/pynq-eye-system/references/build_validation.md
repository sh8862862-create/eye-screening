# 构建与验证

## 输入与前提

本工程历史工具为 Vivado/Vitis HLS 2024.1，器件 `xc7z020clg400-1`。换工具版本需要重新验证。输入包括当前 HLS 源、testbench、Tcl、IP 及板级约束；详情见工程 `build/README.md`。构建命令从工程根目录执行。

```sh
python -B skill/pynq-eye-system/scripts/project.py build hls
python -B skill/pynq-eye-system/scripts/project.py build overlay
```

以上只显示计划。任务明确要求实际构建且工具满足时，为对应命令加 `--execute`。HLS 成功后才能运行 overlay；两步必须使用同一 `EYE_BUILD_DIR`。保留旧构建，选择新的输出目录。

## HLS 和整板验证

1. 当前源为 `src/hls/palm_depthwise_v6.cpp`，testbench 为 `sim/hls/test_depthwise.cpp`。Tcl 顺序为 C 仿真、综合、RTL 协仿真、IP 导出。
2. 现有 testbench 覆盖尺寸 6/12/24/48/96、stride 1/2、确定性随机输入、输出哨兵和非法通道 257；数值差容限为 `1e-4`，不等同于已覆盖模型所有张量形状。改接口或尺寸时补充真实六层参数测试。
3. HLS 导出 IP 成功后再构建联合 Overlay，保持 UART、OLED、语音和按钮外设。核对全部复位连接、DDR 接口、时钟域及 `0x43C10000` 控制地址。
4. 读取本次实际报告：综合/实现是否完成，LUT/BRAM/DSP、DRC、CDC、WNS/WHS、TNS/THS。对未约束路径或 CDC 告警说明原因和处置，不能只抄历史时序数值。
5. bit/hwh 同次生成并记录摘要。对新产物做上板参考比对时，按 [共享 FPGA](shared_fpga.md) 处理所有权和 DMA；构建成功不自动意味着可以替换运行配置。

## CPU ncnn 库

`build ncnn` 默认只显示命令。加 `--execute` 需 ARMv7 Cortex-A9 Linux、g++、CMake、OpenMP 和项目所带 ncnn 源包。Windows x64 本机库不能作为板端 `.so`。核对目标架构、动态依赖、FP32 模型参数/权重和 C 接口，再比较 CPU 后端的同图结果。

## 失败和交付

缺工具、缺 IP 或输出目录冲突时在该阶段停止；保留日志与已完成阶段，下一阶段不得使用旧 IP 冒充新产物。数值失败先查布局、边界、步幅和缓冲区；时序失败先查实际关键路径及约束。交付源摘要、工具版本、命令、阶段退出码、报告和成对产物，并将未执行的协仿真/上板项目标为未验证。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
