# 多人协作约定

每人克隆到自己的本地目录，一个任务一个分支。以 Issue 描述问题、完成条件和模块；通过 Pull Request 合并到 main。尚未邀请队友时由仓库所有者维护，CODEOWNERS 暂统一为 @sh8862862-create。

## 开发步骤

```sh
git switch main
git pull --ff-only
git switch -c fix/gesture-tracking
# 修改并运行 README 的四项离线检查
git add src/ps/gesture/hand_tracker.py
git commit -m "修复弯曲食指跟踪丢失"
git push -u origin fix/gesture-tracking
```

创建 PR，填写问题、改动、测试、未验证事项和回退方式。审核者重点检查受影响的上下游。合并前解决冲突并重跑测试；不要强制推送共享 main。需要更新自己的分支时先 fetch，在个人分支合并 origin/main，再解决冲突。

## 模块与审核

FPGA/HLS 负责 src/hls 和 build/scripts；算法负责 gesture、eye 与模型元数据；外设负责 shared、oled；网页负责 camera_web 与报告。具体队友加入后修改 CODEOWNERS，GitHub 账号需对仓库有写入权限。CODEOWNERS 本身不等于启用强制审核；仓库 Settings 的保护规则需另行设置。

建议 main 要求一名其他成员审核和离线检查通过，禁用强推/删除。私有仓库功能受套餐影响，未开通时按团队约定执行。Actions 首次成功运行后再选择准确的状态检查名称。

## 资源和发布基线

编辑源码不更新所有历史摘要。模型/硬件变更需新资源包、新 resources/manifest.json，并记录来源、兼容性与测试。发布前按实际差异更新 skill/pynq-eye-system/assets/resource_manifest.json 中受影响项，由另一人核对；不能通过盲目重算摘要绕过未知版本差异。

本仓库选择 Release 附件方案，没有启用 LFS。二进制文件导入后被忽略，不应 `git add -f`。第三方许可随版本保留，模型和训练数据授权另行确认。

## 上板与回退

指定一名本批次发布负责人，其他成员先在本地验证。记录板卡占用、提交 ID、模型/bit/hwh 摘要、原文件备份和验收结果；不要多人同时重启服务或加载 Overlay。纯软件修复保持 FPGA generation，硬件变更等待 DMA idle 并释放租约，再由唯一 owner 加载。不得以 CI 通过代替实板验收。

每次发布打明确版本标签，Release 记录与代码提交对应，包含配套资源摘要和回退步骤。网页报告、问卷、相机角色和停止状态按既有约定保留。发布包生成不自动安装，首次 SD 环境搭建仍需单独验证。
