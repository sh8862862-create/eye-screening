# 陌生团队复用指南

目的：把本项目已经形成的工程方法迁移到新的嵌入式视觉系统。复用的是输入契约、判断步骤、故障分支和校验工具，不是照抄 PYNQ 地址或将旧板性能当作新项目指标。

## 最小使用路径

1. 复制整个 `pynq-eye-system/`。只做数值/实现报告校验时，无须复制模型、完整工程或连接板卡；两个校验器只依赖 Python 标准库。
2. 填写 [迁移参数表](../assets/migration_profile.json)，列出器件、地址、所有者、后端、输入输出及资源预算。`null` 表示未确认，不是默认可用值。
3. 按 [任务提示词](../assets/prompt_workflows.md) 选择一个具体任务，把真实源码、报告、配置作为输入。不要把提示词示例当作已发生的实验。
4. 使用下面对应工具或本工程统一入口。为新输入生成独立结果；原黄金参考不得被候选结果覆盖。
5. 使用 [案例记录模板](../assets/case_record.md) 保存来源、失败条件、改动、实际结果和适用边界。

## 黄金参考比对

适用：比较相同输入、布局、数据类型和输出名称的 CPU/PL、框架/ONNX 或两个软件后端。

输入采用 JSON：

```json
{
  "context": {
    "input_sha256": "原输入字节的64位SHA256十六进制摘要",
    "layout": "NCHW",
    "dtype": "float32"
  },
  "outputs": {"logits": [[0.1, -0.2]]},
  "metadata": {"backend": "实际后端", "origin": "真实输出文件或采集命令"}
}
```

以上是格式示意，不是可充当真实结果的样例。context 还可以包含预处理、权重版本等字段，参考与候选必须完全一致；backend 等预期不同的来源放 metadata。嵌套数组结构严格对齐，禁止 NaN、Inf、布尔数和空输出。

```sh
python scripts/compare_results.py --reference reference.json --candidate candidate.json --atol 0.0001 --rtol 0.0001 --output comparison.json
```

输出：逐元素比较数、失败数、最大绝对误差、前十个失败位置、输入文件摘要。容限由任务决定，命令中的值仅示例。规则是 `abs(candidate-reference) <= atol + rtol*abs(reference)`，近零参考不能只使用相对误差。

退出码：0 数值通过，1 数值超差，2 输入契约/格式/文件错误。输出文件必须是新文件。

失败排查顺序：先样本摘要与预处理 → 张量名称和形状 → 浮点/定点及量化 → 步幅/边界 → 层输出 → 整网输出。不要先放宽容限。原型来源为本项目掌检测逐层及整网比对，原证据见 `report/palm_pl_20261028.md`。本通用包装器新增加的验证仅是边界行为测试，不声称已在另一个项目完成实板迁移。

## 资源、时序与 DRC 校验

适用：Vivado 文本格式的 Routed utilization、整体 timing summary 和 DRC summary。当前解析器使用 Slice LUTs、Slice Registers、Block RAM Tile、DSPs 表项；其他工具或资源命名必须先适配解析器。

```sh
python scripts/check_implementation.py --utilization utilization.rpt --timing timing.rpt --drc drc.rpt --limits limits.json --output implementation_check.json
```

limits.json 格式见 [本项目资源上限示例](../assets/implementation_limits.json)。迁移时替换 device 和四类资源预算；器件最大容量与团队性能预算应区分。

通过条件：器件一致、四项资源不超限、setup/hold/pulse slack 不为负、失败端点为零、没有 Error/Critical Warning。保留所有 Warning/Advisory 清单，不能把“无 Error”写成“无 DRC 问题”。报告缺失、总计与表项不一致、未知格式均拒绝给出通过。

此工具不代替 CDC、未约束路径、引脚电压、真实时钟、位流与 HWH 配对、上板功能验收。异步路径及 warning 必须在报告中逐类解释。原方法来源于本项目对 Routed 资源/时序和逐层算子收益的区分；本次仅验证新解析逻辑，未重跑已有硬件检查。

## 迁移失败条件

| 条件 | 应采取的动作 |
|---|---|
| 新器件、接口地址、缓存一致性或 DMA 实现不同 | 重新填写迁移参数，不直接复制 MMIO 地址和 owner 逻辑 |
| 单进程图像系统没有多个 PL 消费者 | 复用资源所有权原则，按实际复杂度简化租约设计 |
| 数据类型、量化尺度或类别顺序不同 | 先建立输出契约，再比较；不能沿用旧浮点容限或阈值 |
| 缺输入、缺原始报告、缺运行环境 | 标记未验证并列出缺失输入，不以模板内容补齐结果 |
| 原报告已经通过且文件与目标不变 | 直接引用已有证据；只验证本次新增或改变的内容 |
| 新硬件/新镜像尚未运行 | 交付安装/构建条件与测试计划，不声称整机复现完成 |

## 已验证效果与边界

- 项目内方法已有手势、丢手重入、部署冲突、黄金参考和性能分阶段记录；效果索引见 [能力说明](capability_contracts.md)。
- 通用校验器的新测试覆盖超差、输入不一致、形状缺失、非有限数、资源超限、负 slack、严重 DRC 和报告不完整。
- 提示词与案例模板来自本项目修订经验，尚无陌生团队独立使用后的稳定成功率数据。模板可复用不等同于“跨项目效果已验证”。
