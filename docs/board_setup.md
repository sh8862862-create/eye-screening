# 板端环境与配置

本仓库不是 SD 镜像或从零自动安装器。运行前核对 PYNQ、Python、OpenCV/NumPy、Pillow/字体、相机角色、systemd unit、动态库和设备访问权限。历史基线见 report/current_release.md，代码接口见综合 Skill。

复制 config/connection.example.json 为忽略的 config/local.json 并填写自己的地址。示例使用文档地址 192.0.2.10，不代表在线板卡。此 JSON 目前是团队连接记录，不会自动覆盖业务服务配置；snapshot 命令显式传 `--url`。模型路径通过 infer 的参数选择。

已有配置和历史发布脚本可能仍包含板端默认地址/路径；它们是历史基线，运行前检查 systemctl cat、实际服务端口和目录。密码或私钥由 SSH 客户端及凭据环境管理，不写入模板、脚本或 PR。

部署前：确认空闲、保存检查号和报告、备份拟覆盖文件及 unit/drop-in、核对 bit/hwh 成对和当前 generation。唯一 FPGA owner 为 pynq-fpga，DMA 未 idle 不强杀持有者。

部署后：校验文件摘要、服务及合并配置、API 状态、已有报告、BTN2 持久停止和本次修改的功能。相机图像、声音、OLED 实屏和 HID 主机动作分别验证，单纯 active 不作为全功能通过。
