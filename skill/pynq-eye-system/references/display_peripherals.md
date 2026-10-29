# OLED 与共享外设

OLED 显示来源于与网页相同的检查状态和报告，不根据“程序还在运行”推断屏幕内容正确。必须比较检查号、预期帧、最后成功写屏帧摘要和实物显示。

维护源：`src/ps/oled/oled_iic.py`、`src/ps/camera_web/oled_result.py`，板端驱动在 `/home/xilinx/ch9329_hid/oled_iic.py`。128×64 SSD1306，地址 0x3C，数据发送控制字 0x40，分块需各自带控制字。共享 owner 负责硬件配置。

## 无硬件预览

```sh
python src/ps/camera_web/oled_result.py --preview /new/oled_preview.png --mock negative
```

需要 Pillow、中文字体；预览图不是实屏成功证据。真实报告预览按该脚本 `--help` 的参数使用，不拼造检查结果。

## 黑屏或内容过期

1. 保留 `/api/devices`、OLED 状态、检查号、日志、预期帧与成功帧摘要。
2. 区分服务退出、I2C NACK/短写、内容未更新、屏幕供电中断。先核对地址、供电与接线；屏幕轻触后黑屏曾发生，但未完成全部电气根因验证。
3. 原生 XIic_Send 无硬超时，允许的恢复次数应受限；无法确认总线状态时停止重复尝试，不绕过 owner 重新加载 Overlay。
4. 恢复后重新初始化并发送当前完整画面，不能只补发失败前部分数据。检查当前启动标识、检查号和帧摘要，再由实屏确认。

共享 UART、GPIO、I2C 的模块只允许 attach 既有 overlay。升级某个驱动后至少回归另一外设的存活，确认 FPGA generation 和下载次数没有意外变化。GPIO 消抖、长按抑制和 I2C 可恢复性按原测试验证；OLED 预览、服务 active、真机屏幕三者分别报告。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
