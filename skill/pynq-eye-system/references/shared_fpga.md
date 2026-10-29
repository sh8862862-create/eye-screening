# 共享 FPGA 排查与恢复

## 输入和边界

输入：合并后的 systemd unit/drop-in、owner 状态、boot ID、generation、bit/hwh 摘要、外设地址、消费者日志。接线和只读状态命令见 [系统契约](system_contract.md)。实现依据为工程 `src/ps/shared/shared_fpga.py` 与 `src/ps/palm/pl_backend.py`。

## 定位顺序

1. 确认唯一配置加载者是 `pynq-fpga`。读取其实际 `ExecStart` 与 drop-in；基础 unit 的旧默认路径不等于当前路径。
2. 核对 owner 的 ready 状态、当前 boot ID 和 generation。进程沿用上次启动或上次加载的描述符时，先退出过期访问路径，重新获得有效租约；不绕过代次校验。
3. 对照 hwh 检查 UART、两路 I2C、GPIO 和掌检测核。PL 核为独立 100 MHz 时钟，外设域为 62.5 MHz；地址和时钟不直接移植到其他板卡。
4. CPU 正常而 PL 失败时，依次查 socket/RPC、服务环境、租约和实际硬件接口，再查数值及缓存同步。生产消费者通过本地 palm 服务使用 PL，不能让第二个测试进程直接争用内核。
5. DMA 超时先记录控制寄存器、错误和 idle 状态。当前后端会保留仍可能被硬件使用的缓冲区与租约；超时不能立即释放或重分配这些内存。

## 故障分支

| 现象 | 检查与处置 |
|---|---|
| 使用一个模块后其他外设失效 | 搜索实际运行路径中的 Overlay 下载；核对 generation 是否意外变化 |
| owner ready 但消费者报 generation unavailable | 比较 boot ID、配置身份、generation 和持有租约的时机 |
| bit/hwh 或寄存器布局不同 | 停止访问，恢复同批且接口匹配的配置；不试探随机地址 |
| palm 服务不可用 | 验证已有 CPU 回退，记录采用的后端和时延，不能称 PL 验收通过 |
| DMA 仍忙或 I2C 原生调用阻塞 | 停止连续重试，保留现场；明确恢复步骤后再处理硬件 |

只有任务要求且已经具备变更授权时才执行恢复：使消费者退出并释放租约，确认 DMA idle，再由 owner 切换同批配置，按实际依赖恢复服务。纯软件修复不需要重载 FPGA。恢复后校验地址与代次，分别验证 palm、UART、OLED、语音和按钮；service active 不是输出正常的充分证据。

完成条件：owner 唯一、消费者代次一致、接口匹配、有关模块功能恢复；纯软件修改前后 generation 保持不变。若硬件未 idle 或代次仍异常，保存状态并报告未闭环，不无限重载。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
