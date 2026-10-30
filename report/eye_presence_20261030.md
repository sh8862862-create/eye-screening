# 眼部照片分析前检测 — 2026-10-30

## 行为

网页“确认并开始分析”和 BTN3 共用 `workflow_worker`。在调用眼病分类器前，读取本次保存照片、校验照片散列并执行眼部检测。未检出时显示“未识别到眼睛，请调整摄像头后重新拍摄。”，保持待确认照片状态，保留“重拍”入口；不创建分析目录、报告或启动手势阅片。重复点击不会发生分析目录冲突。新照片不继承旧检测提示。

通过检测后执行原来的分类、定位和报告流程。模型、OpenCV 组件缺失或照片损坏时报告技术错误并停止分析，不把这些故障解释成“未识别到眼睛”。检测前后均验证照片散列。

## 实现

- `src/ps/camera_web/eye_presence.py`：检测与本次照片的持久化记录。
- `src/ps/camera_web/eye_presence_models/`：两个未修改的 OpenCV Haar 眼部模型，保留许可声明。
- `workers.py`：在眼病分类前执行检测。
- `service.py`：通过 `/api/state.image_validation` 输出检测结果；未检出时保持待确认状态。
- `review.html`：显示提示并提供重拍，页面重载后仍根据本次记录显示检测结果。

`session.json.image_validation` 包含 `status`、`detected`、提示、输入散列、模型、耗时及检测方向。检测框坐标只作为调试证据，注明缩放和旋转空间，不作为病灶定位。

## 验证与边界

18 项离线回归通过，覆盖无眼照片不调用分类器、正常眼图继续分析、照片篡改、重复提交、缺失组件、采集路径、服务状态和精简运行库隔离调用。8 张已有眼部样本通过，黑白灰空白、固定随机噪声和两张室内背景裁剪被拦截。页面测试验证提示、重拍、新照片清除提示、忙碌状态禁用按钮及处理中状态优先显示。

样本来源：眼图取自 `model_retrain/normal7/stage/sample_00.png` 至 `sample_07.png`；背景取自 `tools/gesture_reliability_20261028/validation/frame_00.jpg` 的桌面与墙面区域。实际夹具位于 `sim/eye_presence/fixtures/`。这些样本用于开发验证，不能作为独立准确率评估。带人脸、手部和复杂室内纹理的历史画面在探索性检测中出现过局部误检；该方案仍需以当前眼部摄像头进行现场复核，不能保证所有非眼场景都被拒绝。

此检测不是活体或完整质量检查；拍摄到眼睛但未检出时同样提示重拍。系统不因某个眼病类别分数较低就推断“没有眼睛”。疾病分类阈值、手势模型与 PL 配置保持现有版本。

## 部署状态与复核

2026-10-30 板卡重新上电后已完成部署。部署前通过板端 OpenCV 4.5.4 眼图与背景图检测；部署后确认实际 `/review` 页面已更新、`/api/state` 返回 `image_validation` 字段。直接调用已部署的 `workflow_worker` 和原有 `staged_workflow` 处理临时灰色测试图，返回待确认状态及 `no_eye`，未创建分析目录。该测试耗时约 1808.8 ms，属于单次板端测试，不代表所有图像的耗时。

首次部署备份：`/home/xilinx/eye_presence_20261030_230347/backup/`。当前检查号、历史报告与 FPGA 状态保持不变。首次证据位于 `sim/results/eye_presence_deployment.json` 和 `sim/results/eye_presence_board_verification.json`。旧的 `20261029` 压缩包保留为历史版本；本修订以当前工程目录为准。

### BTN3 运行环境修订

现场反馈“按 BTN3 后没有反应”。日志显示两次 BTN3 请求均被接收，但网页服务的 `opencv-neon.conf` 使子进程加载了缺少 `objdetect` 的精简 OpenCV。此前 SSH 命令行测试没有继承这两个服务环境变量，因而未覆盖真实入口。

修订后，当主进程缺少 `CascadeClassifier` 时，检测使用有 30 秒超时限制的独立子进程；只在该子进程移除 `/opt/opencv-neon` 的 Python 和动态库路径，调用板卡已有完整 OpenCV。主网页与手势进程环境不变。部署冒烟测试也改为读取服务的实际路径覆盖参数。页面在 `PROCESSING` 时优先显示处理进度，避免保存照片仍为待确认状态时界面看起来停留不动。

二次部署备份：`/home/xilinx/eye_presence_20261030_230347/isolated_runtime_backup/`。已通过与服务相同的运行环境测试眼图和背景图；随后对用户刚才保存的 640×480 照片，通过 BTN3 使用的 HTTP 分析入口重新提交。约 16.3 秒后进入 `AWAITING_IMAGE_REVIEW`，返回 `image_validation.status=no_eye`，未创建分析结果。完整请求证据位于 `sim/results/eye_presence_live_action.json`，二次部署证据位于 `sim/results/eye_presence_isolated_deployment.json`。这验证了真实网页工作流；物理按键再次操作的用户反馈仍待确认。

部署入口为工作站的 `python board/deploy_eye_presence.py`，密码仅通过环境变量和标准输入传递，不写入文件。脚本核对旧代码散列、上传新文件、先在板端进行检测冒烟测试，再备份并重启网页服务；失败时回退，不重载 FPGA，不更改检查照片和历史报告。采集或分析忙碌时停止部署。脚本的基线对应本次修改前版本，已部署后重复运行会因基线不匹配而停止，应先核对版本。

实机验证：拍摄无眼场景后点击分析或按 BTN3，应显示未识别提示且无报告；重拍清晰眼部图像后应进入原分析流程。同步检查 `/api/state` 与 `session.json`，避免仅凭网页文字判断后端是否真正拦截。

本机测试需要含 `objdetect` 的 OpenCV。工作站现有精简版没有该组件，测试使用工程外的隔离依赖目录 `tools/eye_presence_20261030/testdeps/`，未替换工作站或板卡已有运行库。

## 协作仓库与完整交付包

协作仓库不包含上述现场证据和照片；它们保留在作者完整工程中。默认回归使用合成图像和隔离工作流，3 项照片检测回归明确跳过；设置 `EYE_PRESENCE_FIXTURES` 后运行全部 18 项。网页测试使用项目 `package.json` 的 Playwright，可通过 `BROWSER_EXECUTABLE` 指定已安装浏览器。部署需显式提供授权夹具目录；新增 Haar 模型随源码发布，保留 XML 内许可。历史资源包的二进制没有变化，代码发布基线和部署映射随本次修订更新。
