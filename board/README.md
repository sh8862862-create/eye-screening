# 配置与阶段发布材料

本目录属于源码协作版。环境与测试见 [总说明](../README.md)，模型和硬件资源见 [资源说明](../docs/resources.md)。历史 archive 保留在完整包，不包含在这里。

## 眼部检测增量部署

见 [修订说明](../report/eye_presence_20261030.md)。`deploy_eye_presence.py` 需要工作站安装 `paramiko`、已验证的 SSH 主机密钥、`PYNQ_SSH_PASSWORD` 和 `--fixtures-dir` 指定的授权眼图/背景样本。固定基线只适用于本修订前版本，不能向已更新的设备重复安装；先检查版本。此脚本不会由 CI 或同步过程执行。

## 新目标安装（2026-11-01）

相机工作流、基础服务和安装工具已经补齐，见 [新板复现说明](../report/reproduction/README.md)。先导入新版资源；install_fresh.py 仅用于新系统或独立 rootfs，不能覆盖已有不同版本。
