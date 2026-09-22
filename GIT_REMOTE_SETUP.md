# 独立 GitHub 仓库连接说明

## 当前边界

- MaiBot 父仓库：`D:\My_file\Mai_lynora_bot\MaiBot`
- 本插件独立仓库：`D:\My_file\Mai_lynora_bot\MaiBot\plugins\Chinese_vocaloid_recommender`
- 父仓库继续使用 `/plugins/*` 忽略插件，不应提交本插件文件。
- 本插件的提交、分支和远端操作必须在插件目录内执行。

## 创建 GitHub 仓库

本机未安装 GitHub CLI，也没有可用的 GitHub 登录连接，因此远端需要在 GitHub 网页创建：

1. 登录 GitHub，进入 `https://github.com/new`。
2. Owner 选择 `welken1405057126`。
3. Repository name 填写 `maibot-chinese-vocaloid-recommender`。
4. 开发阶段不确定是否公开时，先选 `Private`；以后可以改成 `Public`。
5. 不要勾选自动创建 README、`.gitignore` 或 License，避免和本地首次提交产生两套历史。
6. 点击创建仓库。

## 连接并首次推送

在 PowerShell 中执行：

```powershell
cd D:\My_file\Mai_lynora_bot\MaiBot\plugins\Chinese_vocaloid_recommender
git remote add origin https://github.com/welken1405057126/maibot-chinese-vocaloid-recommender.git
git branch -M main
git push -u origin main
```

如果提示 `remote origin already exists`，先检查：

```powershell
git remote -v
```

确认只是地址错误时再执行：

```powershell
git remote set-url origin https://github.com/welken1405057126/maibot-chinese-vocaloid-recommender.git
```

## 日常操作

```powershell
cd D:\My_file\Mai_lynora_bot\MaiBot\plugins\Chinese_vocaloid_recommender
git status
git add <本次修改的文件>
git commit -m "human: describe the change"
git push
```

AI 完成的提交使用 `ai:`，共同完成使用 `pair:`。不要使用 `git add -A` 跨范围收集文件，先用 `git status` 确认每个文件。

## 隐私检查

以下内容由插件 `.gitignore` 排除，不应出现在远端：

- `config.toml` 中的真实群号和管理员 QQ；
- SQLite 数据库；
- 下载的 B 站封面；
- Python 缓存和测试缓存。

公开配置示例是 `config.example.toml`，其中群号和管理员列表必须保持为空。

## 验证仓库没有串线

```powershell
git -C D:\My_file\Mai_lynora_bot\MaiBot status --short
git -C D:\My_file\Mai_lynora_bot\MaiBot\plugins\Chinese_vocaloid_recommender status --short
```

第一条不应出现中 V 插件文件；第二条只应出现本插件文件。父仓库的 `origin` 保持 `Mai-with-u/MaiBot`，插件仓库的 `origin` 应属于 `welken1405057126`。
