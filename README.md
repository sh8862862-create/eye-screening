# PYNQ-Z2 眼表筛查与手势交互

用于研究测试的眼部图像分析、十题语音问卷、网页报告及手势阅片系统。板卡为 PYNQ-Z2 / Zynq-7020；眼部推理、关键点与业务流程在 PS，掌检测的六个深度卷积节点使用 PL。研究模型输出不作为临床有效性证明。

这是多人协作的源码版，模型/bit/hwh/运行库单独保存在带摘要的资源包中。历史工程、训练集、现场照片和检查报告不进入源码仓库。原始完整工程继续保留。

## 本地开始

Windows PowerShell，在本目录执行（建议 Python 3.11 或 3.13）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B sim/verify_repository.py
.\.venv\Scripts\python.exe -B sim/run_gesture_tests.py
.\.venv\Scripts\python.exe -B skill/pynq-eye-system/tests/test_project.py
.\.venv\Scripts\python.exe -B -m unittest discover -s sim/tools -v
```

Linux 对应解释器为 `.venv/bin/python`。离线测试不连接板卡、不使用相机、不发送 HID，也不需要训练集或模型权重。

## 目录与能力

| 目录 | 内容 |
|---|---|
| `src/ps/` | 网页、手势、眼部模型调用、共享硬件和外设源码 |
| `src/hls/`、`src/native/` | 掌检测核和 ncnn 桥接 |
| `sim/` | 协作版校验、手势回归、工具测试、HLS testbench |
| `build/scripts/` | 相对路径构建入口；输出到忽略的 `build/work/` |
| `board/` | 配置与阶段性发布源码，发布时按最终映射选文件 |
| `data/` | 模型元数据、最小数值回归样本、第三方许可 |
| `resources/` | 外部资源路径/大小/SHA256 与源码文件清单 |
| `skill/` | 一个综合技能入口，8 类流程覆盖 14 项能力 |

[系统设计](report/design_report.md) · [历史验收基线](report/current_release.md) · [综合技能](skill/pynq-eye-system/SKILL.md) · [协作约定](CONTRIBUTING.md)

## 推理、构建与发布

先按 [资源使用说明](docs/resources.md) 导入资源包；主机推理另安装 `requirements-host.txt`，不可将其直接用于 ARMv7 板端升级。

```sh
python -B tools/resources.py status
python -B skill/pynq-eye-system/scripts/project.py capabilities
python -B skill/pynq-eye-system/scripts/project.py infer --image /path/eye.jpg --output-dir /new/inspection
python -B skill/pynq-eye-system/scripts/project.py build hls
python -B skill/pynq-eye-system/scripts/project.py release
```

`build` 默认打印计划，需明确加 `--execute` 才构建；`release` 默认只打印映射，不部署。HLS/Vivado 历史版本为 2024.1，目标 `xc7z020clg400-1`；ncnn 构建需要 ARMv7 Linux。板端操作见 [上板约束](docs/board_setup.md)。

## 验证口径

`sim/verify_package.py` 默认执行协作版校验。检查原始完整包时显式传 `--full-package-root`；见资源说明。可编辑源文件由 Git 跟踪，日常 CI 不要求每改一行就重写旧交付清单。发布物由固定资源摘要和选定源码摘要核验，更新发布基线须在 PR 中解释。

GitHub Actions 在 push main、Pull Request 和手动触发时运行离线检查；上传前只能本地验证，不能称云端检查已通过。硬件综合、设备权限、电气连接及现场流程需要另外验收。

## 首次发布

使用私有仓库，操作顺序见 [首次上传清单](docs/github_upload.md)。项目整体许可证尚未选定；已有第三方许可保留。完整训练数据与模型再分发权需单独核对，见 [授权与数据范围](docs/licenses_and_data.md)。

## 2026-10-30：分析前眼部检测

点击分析或按 BTN3 后先检测本次照片。未检出眼睛时显示提示并允许重拍，不生成报告；处理中立即显示进度。修订及已有板端证据见 [检测说明](report/eye_presence_20261030.md)。

新增离线回归：`python -B sim/test_eye_presence.py -v`。默认运行合成图像、状态流转和运行库隔离测试，3 项私有照片回归明确跳过。团队成员取得授权夹具后设置 `EYE_PRESENCE_FIXTURES` 为夹具目录，再运行同一命令；全量应运行 18 项且无跳过。

网页测试需 Node.js 22：`npm ci` → `npx playwright install chromium` → `npm run test:ui`。Linux 首次安装可用 `npx playwright install --with-deps chromium`。测试不访问板卡，输出写到忽略的 `sim/results/`。

Haar XML 随源码保留原许可；训练数据、现场照片、会话记录不上传。原模型/bit/hwh 资源包继续适用；发布载荷须由更新后的源码与映射重新生成。
