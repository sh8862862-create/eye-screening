# WonderEcho 问卷与按键联动

模块先确认 5V 供电、共地及 I2C 3.3V 电平。固件在 `board/firmware/wonderecho/`，协议源表在 `data/voice/`；文件名中的 UART 参数不能替代当前专用 I2C 协议。

地址 `0x34`，播放寄存器 `0x6E`，写 `[0x6E,0xFF,prompt_id]`；答案寄存器 `0x64`，结果 1/2/3 对应有/没有/不确定。读取后零值表示暂无新答案；连续同一答案需观察非零之间的清零，不能只靠灯闪判断收到结果。状态灯为蓝色。

当前十题顺序：R1/R2/R3/Q1/Q3/Q5/Q6/Q7/Q9/Q12；对应 ID `10/11/12/13/15/17/18/19/1B/1E`（十六进制）。保留原固件 ID，不重编号。R1–R3 为既有中止分支；题目删减是缩短原型流程，不代表临床量表效能结论。

## 板端单模块检查

先确认按键服务没有正在运行的问卷；读取答案会消耗模块结果，不应与正式问卷同时监听。脚本在网页工作目录，由共享 owner 提供租约：

```sh
cd /home/xilinx/gesture_demo/camera_web
sudo timeout 25 /usr/local/share/pynq-venv/bin/python3 voice_check.py --play 0x10 --listen 15
```

用“小幻小幻”唤醒后测试有、没有、不确定及重复“没有”。保存播放、答案、清零和时间间隔。原生 I2C 调用没有 Python 硬超时，外部 timeout 是排障边界，触发后需检查租约和服务状态，不能连续无上限重试。

无声先区分唤醒无响应、播放无响应、答案未读到三个环节。检查供电/共地→固件及协议→I2C 地址与寄存器→驱动返回长度→状态机时间条件。不要一开始重烧固件或重载整个 FPGA。

BTN0 驱动问卷，BTN2 的取消检查在 Python 阶段约每 50 ms 执行，已经送入模块的一句录音可能播完；验证点是问卷不再推进、相机不启动或不恢复。最近问卷状态文件会被新检查覆盖，历史答案必须关联检查号另行保留。

维护入口 `src/ps/shared/wonderecho.py`、`voice_check.py`、`button_service.py`。对应离线测试位于原 `shared_fpga/test_wonderecho.py`、`test_voice_buttons.py`，恢复原布局后运行并分别记录板端联调结果。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
