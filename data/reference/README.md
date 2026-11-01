# 黄金参考与测试数据索引

固定输入和原始输出保留在既有目录，不移动、不改写：

- 眼部样例：`data/archive/workspace/model_retrain/normal7/stage/sample_00.png` 等；参考 `smoke_expected.json` 在同一历史stage目录。
- 掌检测真实层张量：`sim/archive/workspace/tools/palm_pl_20261028/validation/real_layer_fixtures.npz`。
- 逐层CPU/PL结果：`sim/archive/workspace/tools/palm_pl_20261028/validation/v6selected_pl_benchmark.json`。
- 手势控制参考：`data/gesture/recorded_results.json`。
- 最新无眼输入样例：`sim/eye_presence/fixtures/`；测试预期由 `sim/test_eye_presence.py` 定义。
- 七类缓存重建一致性：`sim/results/reproduction/dataset_rebuild.json`。

上述路径均相对工程根目录。新增通用数值比较器采用显式context/outputs JSON；如转换历史张量，先保存原输入摘要、布局、dtype和转换来源，不能把转换示例伪装为新板输出。
