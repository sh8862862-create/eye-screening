# 统一命令与输入输出

在工程根目录执行，下文的入口为 `python skill/pynq-eye-system/scripts/project.py`。Python 3.9+；清单、发布准备、状态采集仅用标准库；手势测试需要 NumPy；眼部推理需要 OpenCV 与 NumPy，HTML 报告按现有报告脚本运行。

技能可单独安装到其他目录。此时在子命令前添加 `--project-root /path/to/eye_screening`，也可设置 `EYE_PROJECT_ROOT`。不会自动下载训练数据、创建凭据或部署设备。

| 子命令 | 输入 | 输出与副作用 |
|---|---|---|
| `capabilities` | 工程根目录 | 能力清单、所需环境、缺失资源；只读 |
| `verify` | 当前工程 | 调用全包清单校验；只读 |
| `test` | 当前工程 + NumPy | 调用 29 项手势回归；不发送 HID |
| `locate model_retrain/train_normal7.py` | 原相对路径，可选 `--source-root` | 根据清单查找并校验归档文件；只读 |
| `restore --destination /new/path` | 不存在的目标目录 | 恢复源码原布局；不覆盖已有目录，不部署 |
| `release` | 固定发布映射 | 只打印经过哈希检查的部署计划 |
| `release --output /new/release.tgz` | 不存在的文件，父目录已存在 | 生成 rootfs 与部署清单；不安装 |
| `snapshot --url http://192.168.2.99:8080 --output /new/state.json` | 服务地址 | GET `/api/state` 和 `/api/devices`；保存观测及错误，不发送业务动作 |
| `diagnose --snapshot /path/state.json` | 上一条输出或状态 JSON | 诊断线索和下一步；不自动重启 |
| `build hls`、`build overlay`、`build ncnn` | 已安装工具 | 默认显示命令；加 `--execute` 执行构建 |
| `infer --image /path/eye.jpg --output-dir /new/result` | 单张图像 | 使用固定七类模型与元数据，生成 JSON、叠加图、HTML；`--localize` 加定位网络 |
| `report --result /path/result.json --image /path/eye.jpg --output /new/report.html` | 已有真实结果与图像 | 重新生成自包含报告，不重新推理 |

所有显式输出拒绝覆盖已有同名文件或目录。构建可能耗时较长，只在请求构建且依赖满足时使用 `--execute`。退出码 0 为该操作成功，1 表示测试或状态采集失败，2 表示输入或资源错误；具体底层工具可以返回其他非零码。

原始训练与历史部署脚本具有目录依赖。`locate` 适合检查源码；实际运行前用 `restore` 还原完整布局，再按相应流程处理外部路径。不要直接在扁平归档目录运行这些脚本。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
