# 首次上传与后续邀请

此协作副本使用 main 分支。以下步骤用于首次上传或其他维护者重新建立远端，执行前先用 git status 和 git log 检查本地状态；已有首次提交时不必重复提交。创建私有空仓库 eye-screening 时不预置 README/许可证。

在本目录执行以下命令，邮箱替换为 GitHub Settings → Emails 显示的提交邮箱：

```sh
git config user.name "sh8862862-create"
git config user.email "你的提交邮箱"
git add .
git diff --cached --stat
git status
git commit -m "建立眼表筛查工程协作基线"
git remote add origin https://github.com/sh8862862-create/eye-screening.git
git push -u origin main
```

remote 地址是建议值，先核对已创建仓库的实际地址；已有 origin 时先 `git remote -v` 检查，不重复添加。首次推送按凭据管理器提示在浏览器登录，避免把凭据写入命令。

上传后检查 main、Private、README、Skill 和 Actions。CI 的四个矩阵任务应完成后再启用相应必需检查。Settings → Collaborators → Add people 邀请队友，队友接受后克隆并用任务分支提交 PR。

资源包不进入 Git 历史。团队需要推理/上板时，将经过授权的资源包附到匹配的私有 Release，或通过授权的文件传输给队友，按 docs/resources.md 导入。完整归档与训练数据另行管理。

官方参考：[首次上传](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)、[CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)、[分支保护](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)。
