# 完整工程补充与复现入口

## GitHub 源码版范围（2026-11-01）

此仓库同步最新提交版的可公开源码、技能、安装构建入口和设计报告。训练数据、现场照片、原始会话、完整历史归档不在公开仓库中。下文涉及这些内容的路径指本地完整提交包；运行资源从 [资源说明](../../docs/resources.md) 导入。已有结果的汇总在 [验证记录](../validation/validation_summary.json)，本次仅同步，不重复硬件与旧工程测试。新 SD 卡完整验收、完整重训练、重新综合实现仍未执行。


版本：2026-11-01。保留 `src/sim/build/board/data/skill/report` 七类目录，所有原始历史归档仍在原位置。原压缩包未覆盖。

本版补齐当前板端相机工作流、网页基础服务及 drop-in、眼睛检测门控、ARM 运行环境、字体、训练数据与训练入口、硬件源码和全包校验。板端采集为只读操作，没有替换文件、加载 FPGA、停止服务或清除报告。

## 1. 材料与版本

| 内容 | 本版位置 |
|---|---|
| 当前网页、眼睛检测及双相机工作流 | `src/ps/camera_web/`、`src/ps/camera_switch/` |
| 摄像机配置示例与原板参考 | `board/config/camera_roles.example.json`、`camera_roles_original.json` |
| 网页等基础服务与 NEON/字体配置 | `board/config/`、技能 `assets/` 中已有配置 |
| 新系统安装、相机配置、环境检查 | `board/install_fresh.py`、`configure_cameras.py`、`preflight.py` |
| OpenCV 运行库快照与恢复器 | `data/runtime/`、`board/restore_runtime.py` |
| OpenCV 4.5.4 源码与编译缓存证据 | `data/third_party/opencv-4.5.4.tar.gz`、`build/reports/opencv_neon/` |
| 七类训练数据和便携入口 | `data/training/`、`src/training/`、`build/scripts/train_normal7.py` |
| 原三类模型及定位训练源码 | `src/training/eye_base/` |
| CH9329 PCB 原生工程 | `data/hardware/ch9329_usb_hid.eprj2` |
| 最新设计报告 | `report/design_report_20261101_v5.docx` |
| 现有板端来源及依赖记录 | 本目录 `board_source_snapshot.json`、`python_packages.txt`、`system_packages.txt` 等 |
| 全文件校验与本次验证 | `sim/verify_full_package.py`、`sim/results/reproduction/` |

原包中的说明和实验记录按原日期保留；部署、训练和验收以本说明及对应源码为准。历史 `board/releases/` 内旧 worker 不应覆盖 `src/ps/camera_web/workers.py`。技能发布映射已选定配套版本。

## 2. 实际验证范围

- 重新准备七类数据：正常图像 500 张；训练/验证/测试为 1384/331/338。`images_cls/labels/mask/splits` 四个数组与历史 `dataset7.npz` 完全一致。
- 用已有 balanced 检查点重新导出 ONNX，六个样例与 PyTorch 的最大 logits 绝对误差为 `4.00543212890625e-05`。这是导出复验，没有重新训练全部 epoch。
- 手势、相机工作流、无眼拦截、全新目录安装及技能工具已运行离线测试。具体计数与跳过项见 `sim/results/reproduction/offline_tests.log`。
- 板端只读环境检查通过：相机路径、工作流导入、PYNQ、NumPy、Pillow、vendored Waitress、完整 OpenCV、掌检测桥接库、ncnn 库及六个基础 unit 存在。
- OLED：**验收通过**。2026-11-01 用户说明异常源于接线未接好，重新接线后确认“已接好，显示正常”。此项为用户实物验收；先前采集的失败状态保留作为故障过程记录。

本次尚未在另一张空白 SD 卡上完成安装及整机验收；未重跑 HLS/Vivado 全量综合、实现或全部训练。包内的安装/构建步骤可用于复现，不能将文件完整、离线测试或已有板端环境检查替代这些独立验收。

## 3. 主机校验与离线运行

Windows 建议解压到较短目录，例如 `C:/eye/eye_screening`；长历史文件名需支持长路径的解压器。Python 校验器已兼容 Windows 扩展路径。以下命令从工程根目录执行。

```sh
python -B sim/verify_package.py
python -B sim/verify_full_package.py
python -m venv ../eye-host-env
```

Windows 使用 `../eye-host-env/Scripts/python.exe`，Linux 使用 `../eye-host-env/bin/python` 代替下列 `HOST_PYTHON`：

```sh
HOST_PYTHON -m pip install -r build/requirements-host.txt
HOST_PYTHON -B sim/run_all_tests.py
HOST_PYTHON -B skill/pynq-eye-system/scripts/project.py infer --image data/archive/workspace/model_retrain/normal7/stage/sample_00.png --output-dir ../new-eye-result --localize
```

主机验证固定 NumPy 2.2.6、OpenCV 4.10.0.84、Pillow 12.0.0；不要使用默认 OpenCV 5 环境代替眼睛检测验收。Windows 下 4 项 Linux 进程组测试跳过，需在 Linux 上再执行。网页测试入口是 `sim/test_eye_presence_ui.cjs`，需要 Playwright 与可启动的 Chromium/Edge。

## 4. 全新板卡安装

### 基础环境

板卡为 PYNQ-Z2 / Zynq-7020 / ARMv7。采集环境为 PynqLinux 3.0（Ubuntu 22.04）、内核 `6.6.10-xilinx-v2024.1-g3c0eca68c652`、Python 3.10.4、PYNQ Python 包 3.1.1；操作系统版本和 Python 包版本不是同一个编号。基础镜像应提供 `/usr/local/share/pynq-venv/bin/python3`、XRT、PYNQ、`clear_pl_statefile.service` 等。

本工程包不是可直接烧录的 SD 卡镜像；基础镜像本身未收入 ZIP。使用匹配的 PYNQ-Z2 基础镜像，在独立 SD 卡上安装。若镜像版本不同，先对照本目录系统及 Python 依赖清单，不能直接将本版 ARM 动态库加载到任意系统。

原板完整 OpenCV 来自 `python3-opencv 4.5.4+dfsg-9ubuntu4`，NumPy 为 1.21.5、Pillow 为 9.0.1。新镜像缺失时可从匹配系统的软件仓库安装以下组件，并核对实际版本：

```sh
sudo apt-get update
sudo apt-get install python3-opencv python3-numpy python3-pil libgomp1 libdc1394-25 libjpeg-turbo8 libpng16-16 zlib1g
```

完整 OpenCV 与 `data/runtime/` 的精简 NEON 版必须并存：精简版用于手势，完整系统版用于独立眼睛检测进程。不要用精简版全局覆盖系统 cv2。Waitress 3.0.2 已随源码 vendoring，并保留 wheel 与许可证，无须联网安装。当前 PMODA UARTLite 路径不依赖 USB 串口；可选串口路径另见 `build/requirements-board-extra.txt`。

### 网络与相机角色

当前服务默认地址 `192.168.2.99/24`，电脑直连参考地址 `192.168.2.1/24`。原板网络配置见 `network_reference.txt`。改变 IP 时应同时修改网页启动地址、等待地址脚本、按钮/HID/OLED 使用的 URL，不能只改某一个入口。

在目标板上查看 `ls -l /dev/v4l/by-path`，实际确认两路摄像机用途后生成角色文件。下列端口仅来自原板参考，换 HUB 或 USB 接口后必须改成目标板路径。

```sh
python3 board/configure_cameras.py --eye /dev/v4l/by-path/platform-ci_hdrc.0-usb-0:1.4:1.0-video-index0 --gesture /dev/v4l/by-path/platform-ci_hdrc.0-usb-0:1.3:1.0-video-index0 --output ../camera_roles_target.json --confirm-roles --check-devices
```

示例配置默认 `roles_confirmed=false`。`camera_roles_original.json` 是已配置旧板的证据，不由安装器自动采用。

### 安装应用与运行库

先运行不带 `--apply` 的命令检查计划。安装器拒绝覆盖内容不同的现有文件；已有板卡升级应另走备份和回退流程，不能强制执行全新安装器。

```sh
sudo /usr/local/share/pynq-venv/bin/python3 board/install_fresh.py --camera-config ../camera_roles_target.json
sudo /usr/local/share/pynq-venv/bin/python3 board/install_fresh.py --camera-config ../camera_roles_target.json --apply
sudo /usr/local/share/pynq-venv/bin/python3 board/restore_runtime.py
sudo /usr/local/share/pynq-venv/bin/python3 board/restore_runtime.py --apply
sudo /usr/local/share/pynq-venv/bin/python3 board/preflight.py --output ../new-board-preflight.json
```

也可加 `--rootfs ../staged-rootfs` 在独立目录验证安装载荷，避免改动当前系统。源码安装器会创建应用目录、填写文件权限与 xilinx 所有权；不自动加载 FPGA或启动服务。运行库恢复器校验哈希并拒绝已有运行库目录。

环境检查全部通过后，在新板卡执行：

```sh
sudo systemctl daemon-reload
sudo systemctl enable pynq-fpga pynq-palm pynq-camera-web pynq-buttons ch9329-hid eye-oled
sudo systemctl start pynq-fpga
sudo systemctl start pynq-palm
sudo systemctl start pynq-camera-web
sudo systemctl start pynq-buttons ch9329-hid eye-oled
systemctl is-active pynq-fpga pynq-palm pynq-camera-web pynq-buttons ch9329-hid eye-oled
curl -fsS http://192.168.2.99:8080/api/state
```

首次上板核对 PMODA/PMODB、电压和共地，按已有接线说明连接。WonderEcho 使用包内固件与问卷协议，5V 供电；固件复制进包不代表目标模块已经烧录。

## 5. 构建与训练

### 硬件与原生库

Vivado/Vitis HLS 2024.1 的 HLS、RTL 协仿、综合、实现入口保持不变：

```sh
vitis_hls -f build/scripts/build_palm_hls.tcl
vivado -mode batch -source build/scripts/build_palm_overlay.tcl
```

ARMv7 Linux 上可从包内源码构建库。先满足 CMake、g++、Python 3.10 开发头、NumPy 头及对应图像/视频系统开发包，参考捕获的 `cmake_cache.txt`。

```sh
sh build/scripts/build_opencv_neon.sh
sh build/scripts/build_palm_native.sh
sh build/scripts/build_ncnn.sh
```

OpenCV 脚本默认单线程编译并输出独立 rootfs/tar，不覆盖板端 `/opt`。掌检测桥接库必须链接与服务实际使用的同一 OpenCV。新构建产物先做 ABI/数值比对和完整验收，再替换交付基线。

### 七类数据、训练和导出

训练与主机界面测试使用独立环境。`build/requirements-training-observed.txt` 记录本次数据准备/导出所在主机的包版本，原始算法和十二轮训练策略保留。数据包含历史六类缓存、正常图像 500 张及来源 CSV；已有六类/七类检查点仍在历史归档目录。

```sh
python -B build/scripts/train_normal7.py prepare --output ../experiment_normal7
python -B build/scripts/train_normal7.py train --output ../experiment_normal7 --variant balanced
python -B build/scripts/train_normal7.py export --output ../experiment_normal7 --variant balanced
```

只需要复用历史缓存时，为 prepare 增加 `--use-saved-dataset`。所有实验写到新目录，训练拒绝已有同名 variant 输出。导出默认保持检查点中的阈值，本次 balanced 检查点仍为正常阈值 0.98；当前运行配置 0.85 是后续单独调整，两者必须区分。导出候选模型不会自动部署或覆盖现有阈值。

原定位模型训练源码、数据缓存、弱矩形框标注清单和检查点在 `src/training/eye_base/`、`data/training/eye_base/` 和 `data/training/checkpoints/localizer/`。该源码作为 Python 包运行，例如：

```sh
python -m src.training.eye_base.train --task localizer --data data/training/eye_base/dataset.npz --output ../new_localizer --device cpu
```

该路径另需 scikit-learn，原始数据获取和标签审核说明见随附清单。缓存足以作为该训练入口输入，但不能将缓存中的图像标签宣称为经过临床审核。未确认再分发许可的素材只用于本地研究交接，不随意公开发布。

## 6. 新目标完整验收

记录镜像、依赖、bit/hwh、模型及应用哈希后依次确认：

1. BTN0 开始问卷，语音播报和答案保存正常。
2. BTN1 拍照，照片属于眼部摄像机且保持待确认。
3. BTN3 确认分析；桌面/墙面图片显示“未识别到眼睛”，不生成分类报告；合格眼部样例进入分析。
4. BTN2 停止问卷和相机，保持网页打开也不自动恢复。
5. 报告、OLED 显示和检查号一致。
6. 捏合解锁；食指伸直上滚、弯曲下滚；手离开后不自动锁定，回到画面不跳动，丢帧期间不持续输出旧动作。
7. 检查 FPGA owner/generation、掌检测 PL、ncnn 后端、I2C/UART 错误和长期运行状态。

本次已有板的只读检查和用户 OLED 验收记录在补充报告中；更换镜像或硬件后的上述整机验收仍应重新执行。功耗、长稳、独立数据集效果及端到端 P95/P99 不因补齐文件而自动变成已测指标。
