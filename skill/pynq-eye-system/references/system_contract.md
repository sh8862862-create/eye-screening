# 系统基线与接口契约

基线为 2026-10-29 已验收的 PYNQ-Z2 工程。板卡 Zynq-7020，ARM Cortex-A9；板端 Python `/usr/local/share/pynq-venv/bin/python3`，PYNQ 3.1.1。当前板卡地址通常为 `192.168.2.99`，网页端口 8080；应以连接测试为准，不能将旧地址视为设备在线证据。

| 资源 | 地址或接线 | 约束 |
|---|---|---|
| CH9329 UARTLite | 0x41200000，PMODA A1 TX/Y18、A2 RX/Y19 | 9600 8N1，模块查询版本 V3.4；USB 接电脑 |
| OLED AXI I2C | 0x41600000，PMODB B1 SCL/W14、B2 SDA/Y14 | SSD1306 128×64，七位地址 0x3C |
| WonderEcho AXI I2C | 0x41800000，PMODB B9 SCL/V12、B10 SDA/W13 | 七位地址 0x34；模块 5V 供电，PL 信号 3.3V |
| 按钮 GPIO | 0x40000000，BTN0–3：D19/D20/L20/L19 | 输入、消抖、长按不重复触发 |
| 掌检测核 | 0x43C10000 | 100 MHz；共享 owner、租约与 DMA 生命周期 |

更换板卡不能照搬地址或管脚。确认原理图、XDC、供电、共地、上拉与电平；PMODB B12 不是 5V 电源端。

关键服务：`pynq-fpga`、`pynq-palm`、`pynq-camera-web`、`pynq-buttons`、`ch9329-hid`、`eye-oled`。`pynq-fpga` 允许 active(exited)，长期消费者应检查 active(running)、启动标识与错误日志。仅看 service active 不能证明相机帧、OLED 内容或 HID 正常。

共享状态位于 `/run/pynq-shared-fpga/`，掌检测 socket 为 `/run/pynq-palm/palm.sock`。报告与问卷在网页 data 下按既有程序管理，整理或部署不能清空现场检查数据。

初始检查在板端可运行：

```sh
systemctl status pynq-fpga pynq-palm pynq-camera-web pynq-buttons ch9329-hid eye-oled --no-pager
/usr/local/share/pynq-venv/bin/python3 /opt/pynq-shared/shared_fpga.py status
systemctl cat pynq-fpga pynq-camera-web ch9329-hid
```

记录服务 override 的实际合并结果。基础 unit 保留过往默认值，当前 palm 与 pose drop-in 才决定加载路径和推理后端。凭据通过连接环境提供，不写入技能、配置模板或日志。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
