# 版本与文件完整性

## 适用与输入

用于迁移、发布准备、缺文件或不同批次混装。输入是工程根目录、原始文件清单、当前入口清单和拟修改文件。清单只能证明相对于既定版本的字节一致；不能证明模型有效或设备可用。

## 操作

在工程根目录执行：

```sh
python -B skill/pynq-eye-system/scripts/project.py capabilities
python -B skill/pynq-eye-system/scripts/project.py verify
python -B skill/artifact_integrity/verify_manifest.py --root . --manifest report/organization/current_sources.json
```

1. `file_inventory.json` 描述归档原始输入；`current_sources.json` 描述整理后的选定入口；技能 `resource_manifest.json` 固定本版使用的资源。三者用途不同，不因某个历史目录日期较新就替换最终文件。
2. 最终网页 worker 取 `src/ps/camera_web/workers.py`；控制器和跟踪器取 `src/ps/gesture/`。`src/ps/palm/workers.py` 是掌检测阶段材料，不是最终网页 worker。
3. 文件缺失时用 `locate 原相对路径 --source-root 来源键` 查询原件。只在确需运行旧脚本时，用 `restore --destination 新目录` 恢复原布局。恢复目标必须位于工程外且原本不存在。
4. 发现哈希差异时，比较源/目标、大小、版本、修改理由及调用者。受控修改应保留旧摘要、差异、测试证据，再更新明确受影响的版本清单；不要全量重算后直接称校验通过。
5. 复制完成后在目标目录再次校验。Windows 长路径失败要查看具体失败路径；目录存在和文件数量一致均不能替代字节校验。

## 判断与失败处理

| 观测 | 下一步 |
|---|---|
| 当前文件与历史发布包不同 | 对照当前入口清单；可能是合法叠加修订 |
| bit/hwh 只有一个摘要匹配 | 暂停使用，找回同次构建的成对文件 |
| 历史绝对路径不存在 | 检查原布局及外部依赖，不猜测替换目录 |
| 清单与文件同时来自未知来源 | 另核可信来源；哈希本身不提供来源认证 |

完成条件：拟交付文件可追溯、目标校验无错误、差异均有解释。输出失败路径列表和处置结果，不能以跳过缺失项获得“全包通过”。原项目校验脚本继续保留；新技能的固定文件名 `SKILL.md` 由本次兼容补丁允许。


本文件的历史工程路径按 [协作版资源与归档说明](../../../docs/resources.md) 访问。协作版默认 verify 不要求完整归档，工具测试不要求模型或板卡。
