# 中文 Vocaloid 推荐

用于 MaiBot 的 QQ 群中文 Vocaloid 曲目收录、随机推荐和管理插件。

当前版本为 `0.2.0` 自用发布版，暂未上架 MaiBot 插件市场。插件使用 B 站公开网页接口读取视频信息，不需要单独部署服务器或数据库。

## 功能

- 使用 BV 号、AV 号、B 站视频链接或 `b23.tv` 短链收录曲目。
- 跨群共享曲库，自动避免在同一聊天中连续推荐最近出现的曲目。
- 推荐封面、标题、UP 主、播放量和原视频链接；封面不可用时自动退化为纯文本。
- 按需刷新视频元数据，B 站临时不可用时继续使用曲库中的旧信息。
- 支持上传者、投稿来源群管理员和全局管理员软删除曲目。
- 提供上传和推荐限流、链接白名单、响应大小限制及本地封面缓存清理。

## 环境要求

- Python 3.12 或更高版本
- MaiBot 1.0.0 至 1.0.11
- MaiBot Plugin SDK 2.x
- 可用的 NapCat 适配器；群管理员删除权限需要其群成员信息接口

SQLite、`aiohttp` 等运行依赖已经由 Python 或 MaiBot 提供，不需要额外安装依赖，也不需要启动独立数据库服务。

## 安装

进入 MaiBot 的 `plugins` 目录，将仓库克隆为独立插件目录：

```powershell
cd D:\path\to\MaiBot\plugins
git clone https://github.com/welken1405057126/maibot-chinese-vocaloid-recommender.git Chinese_vocaloid_recommender
```

然后重启 MaiBot。若 MaiBot 已经运行，也可以通过插件管理命令加载：

```text
/pm plugin load welken.Chinese_vocaloid_recommender
```

首次加载时，MaiBot 会根据插件配置模型在插件目录自动生成 `config.toml`。也可以先复制 `config.example.toml` 为 `config.toml`，再修改其中的群号和管理员 QQ 号。

`config.toml` 已被 Git 忽略，不应提交到公开仓库。

## 更新

更新前建议先停止 MaiBot，并备份完整数据目录，随后在插件目录拉取新版本：

```powershell
cd D:\path\to\MaiBot\plugins\Chinese_vocaloid_recommender
git pull --ff-only
```

重启 MaiBot，或执行：

```text
/pm plugin reload welken.Chinese_vocaloid_recommender
```

从旧版升级时，插件会自动迁移兼容的 SQLite 曲库结构。插件发行版本与配置结构版本相互独立，因此 `0.2.0` 版本继续使用 `config_version = "0.1.0"` 是正常的。

## 配置

### `[plugin]` 插件基础设置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用插件；关闭后所有命令均不响应 |
| `config_version` | `"0.1.0"` | 配置结构版本，由插件维护，不要手动修改 |

### `[scope]` 作用范围

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `allowed_group_ids` | `[]` | 允许使用插件的群号；使用字符串，空列表表示不限制群 |
| `allow_private` | `true` | 是否允许在私聊中使用插件 |
| `library_mode` | `"global"` | 当前版本固定为跨群共享曲库，请勿改为其他值 |

试运行时建议只在 `allowed_group_ids` 中填写测试群号，例如：

```toml
[scope]
allowed_group_ids = ["123456789"]
allow_private = false
library_mode = "global"
```

### `[permission]` 删除权限

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `global_admin_ids` | `[]` | 全局管理员 QQ 号；使用字符串，空列表表示没有全局管理员 |
| `allow_group_admin_delete` | `true` | 是否允许群主或群管理员删除来源于本群的投稿 |

上传者可以删除自己上传的曲目。群主或群管理员只能在投稿来源群删除该群投稿；跨群管理员不获得删除权限。群管理身份查询失败时，插件按无权限处理。

### `[recommendation]` 推荐设置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `recent_exclusion_count` | `5` | 避开当前聊天最近推荐的 N 首；曲库不足时自动放宽 |
| `metadata_refresh_days` | `2` | 标题、UP 主、封面来源和播放量超过 N 天后按需刷新；`0` 表示每次刷新 |

### `[rate_limit]` 限流

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `upload_cooldown_seconds` | `60` | 同一用户两次合法上传尝试的最短间隔秒数 |
| `upload_daily_limit` | `10` | 同一用户按北京时间计算的每日成功收录上限 |
| `stream_upload_attempts_per_minute` | `20` | 同一聊天流每分钟最多受理的上传尝试数 |
| `recommend_cooldown_seconds` | `5` | 同一聊天流两次推荐命令的最短间隔秒数 |

限流记录保存在 SQLite 中，重启后仍然有效。

### `[network]` B 站只读请求

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `request_timeout_seconds` | `12` | 一次上传或刷新流程的总超时秒数 |
| `max_redirects` | `3` | `b23.tv` 短链允许的最大跳转次数，每次跳转都会重新检查域名 |
| `metadata_max_bytes` | `2097152` | 视频元数据响应上限，默认 2 MiB |
| `max_cover_bytes` | `8388608` | 单张封面响应上限，默认 8 MiB |

B 站接口地址、视频链接域名、封面域名和请求头由代码固定，配置文件不能将请求改到其他主机。

### `[cover_cache]` 封面缓存

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `cleanup_interval_hours` | `24` | 惰性清理任务的最小间隔小时数 |
| `deleted_retention_days` | `30` | 已软删除曲目的封面保留天数 |
| `orphan_retention_days` | `7` | 无数据库记录引用的孤儿封面保留天数 |
| `temp_retention_hours` | `24` | 下载中断后临时文件的保留小时数 |
| `max_cache_mib` | `256` | 缓存总大小上限；超出后优先清理孤儿和软删除曲目的封面 |

## 命令

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `/中v帮助` | 所有人 | 显示命令说明 |
| `/来首中v`、`/随机中v`、`/中v随机` | 所有人 | 随机推荐一首曲目 |
| `/上传中v [附带文字] <B站链接或BV/AV号>` | 所有人，受频率限制 | 从整段参数中提取并收录一个合法视频 |
| `/中v删除 <ID>` | 上传者、来源群管理员或全局管理员 | 软删除指定曲目 |

所有命令必须以半角 `/` 开头。数字 ID 仅用于删除和内部审计，当前没有按 ID 推荐命令。

推荐消息示例：

```text
----- 随机推荐(≧▽≦) -----
[封面]
《花骨朵》亚细亚旷世奇才/洛天依
亚细亚旷世奇才 · 12.3万播放
--------------------------
https://www.bilibili.com/video/BV16veP6eEeC
```

## 数据与备份

运行数据不在插件源码仓库中，而在：

```text
MaiBot/data/plugins/welken.Chinese_vocaloid_recommender/
├── tracks.sqlite3
└── covers/
```

`tracks.sqlite3` 包含曲目、上传者 QQ、来源群、删除记录、限流和推荐历史；`covers/` 是可以重新下载的封面缓存。

最稳妥的备份方式是先停止 MaiBot，再复制整个 `welken.Chinese_vocaloid_recommender` 数据目录。恢复时同样先停止 MaiBot，再把备份放回原位置。仅备份插件源码不能保留曲库。

## 常见问题

### 命令没有回复

- 确认命令使用半角 `/` 开头。
- 检查 `[plugin].enabled`、`allowed_group_ids` 和 `allow_private`。
- 执行 `/pm plugin list_enabled`，确认插件已经加载。
- 修改配置后执行插件重载或重启 MaiBot。

### 上传时提示“B站没回应，待会再试”

B 站公开接口可能暂时不可用、超时或触发访问限制。稍后重试即可；插件不会在接口校验失败时写入不完整曲目。

### 群管理员无法删除曲目

确认曲目确实来源于当前群，并且 NapCat 适配器能够响应群成员信息查询。跨群管理员不能删除其他群来源的投稿。

### 推荐没有封面

封面下载、缓存或图文发送失败时，插件会静默退化为纯文本推荐，不影响曲目收录和链接发送。

### 删除后还能恢复吗

当前只有软删除命令，没有群聊恢复命令。记录仍保存在数据库中，但再次上传同一视频会提示它曾经被删除。

## 已知限制

- 当前仅实现全局共享曲库，`library_mode` 的其他模式尚未开放。
- 视频信息依赖 B 站公开网页接口，不承诺长期稳定性。
- 插件尚未上架 MaiBot 插件市场，目前通过源码仓库安装和更新。
- 当前兼容性只验证到 MaiBot 1.0.11。

## 开发检查

在插件目录运行：

```powershell
..\..\.venv\Scripts\python.exe -m compileall -q .
..\..\.venv\Scripts\ruff.exe check .
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
```

自动测试不会访问真实 B 站，网络响应和短链跳转均使用本地模拟。正式发布前仍应在测试群验证上传、推荐、重启后数据保留及各类删除权限。

## 隐私与内容说明

插件仓库不附带 B 站视频或封面文件。运行时会保存投稿者 QQ、来源群号和必要的视频元数据，并按需缓存封面；请根据实际使用范围保管数据并设置访问权限。视频标题、封面及链接内容的相关权利归原权利人所有。

## 许可证

本项目采用 GPL-3.0-or-later，详见 [LICENSE](LICENSE)。
