# 失败案例与排查清单

本清单是历史案例的结构化提炼；结果以所列证据为准，不把现象直接等同于根因。

| 触发条件/现象 | 分层排查步骤 | 停止条件 | 经验出处与已知效果 |
|---|---|---|---|
| 多服务操作 PL 后 UART/I2C 或模型失效 | 查 bit/hwh→owner→generation→消费者租约→DMA | DMA 未 idle、版本不一致时停止覆盖 | `report/palm_pl_20261028.md`；共享 owner/租约方案 |
| 语音无唤醒或不播报 | 5V/共地→唤醒回应→协议→指令/答案链 | 供电与电平不明不改协议 | `report/voice_handoff_20261025.md`；用户确认唤醒和问卷 |
| OLED failed/黑屏 | 供电接线→地址/ACK→共享 I2C→字体→显示任务 | 硬件接线未确认不认定软件故障 | `sim/results/reproduction/oled_user_acceptance.json`；重新接线后用户验收通过 |
| 食指弯曲后丢手 | 核对最新帧→掌检测→ROI角点/旋转→姿态置信度→有界重试 | 无新姿态时停止输出，不复用旧动作 | `report/gesture_reliability_20261028.md`；同批回放10/12到12/12，非总体准确率 |
| 离开画面导致模式超时或返回跳动 | 分离模式保留和运动基线→重入首帧静默→检查结果年龄 | 过期结果不得继续输出 | 用户反馈“两种方式都能继续控制，没有跳动”；`sim/gesture/` |
| 推理更快但页面迟滞 | 分开采集/网络/RPC/状态机/HID/主机焦点计时 | 缺同口径样本不得计算加速比 | `report/gesture_latency_20261028.md`；模型和端到端指标分开 |
| 眼睛检测模块缺失 | 核对服务PYTHONPATH→objdetect→独立完整OpenCV→XML | 检测器技术错误不得当作有效眼部输入 | `report/reproduction/README.md`、`sim/test_eye_presence.py`；18项测试已通过 |
| 新板导入 workflow 失败 | 检查 staged_workflow→sequential_camera→工作目录/相机配置 | 文件缺失不靠路径回退掩盖 | `board/preflight.py`；缺失链已补入并完成导入检查 |
| 复制多个发布目录后行为回退 | 以最终映射逐文件选择→摘要→基础unit与drop-in | 不覆盖未知版本或运行中的任务 | `skill/pynq-eye-system/assets/deployment_map.json` |
| 训练/报告阈值口径不一致 | 分类/定位预处理分别查→类别顺序→权重→阈值来源 | 不用训练0.98指标描述运行0.85效果 | `report/design_report_20261101_v5.docx`；历史复算明确2/238误判正常 |
| Windows解压/校验误报缺文件 | 检查路径长度→短目录/扩展路径→原ZIP清单 | 未确认文件存在前不重写哈希 | `report/reproduction/README.md`；扩展路径解决误报 |

表中效果均为本项目证据。陌生项目需重新确认硬件、协议和数据契约，但不必重复运行没有变化的历史实验。
