# 训练入口

七类模型使用工程根目录的 `build/scripts/train_normal7.py`；该入口写入独立实验目录。`train_normal7.py` 已改为包内相对数据路径。其他历史训练/部署辅助脚本保留原语义，不能批量执行；完整用法见 `report/reproduction/README.md`。

`eye_base` 是原三类分类及弱定位包，需作为 `python -m src.training.eye_base.train` 运行，不是当前七类分类模型的替代入口。

公开源码不包含训练集或检查点。七类训练按 EYE_NORMAL_DATA、EYE_DISEASE_DATA、EYE_BASE_CHECKPOINT 指定已授权输入；六类旧训练辅助模块的 EYE_TRAINING_ROOT 指向包含 dataset6.npz 与 runs/best6.pt 的目录。默认均不下载数据。
