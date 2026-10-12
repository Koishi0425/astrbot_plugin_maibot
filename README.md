# astrbot_plugin_maibot

[![python3](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

基于 [AstrBot](https://astrbot.app) 框架的街机音游 **舞萌DX** 查分插件。

当前版本：**v1.7.0**。更新内容见 [CHANGELOG](CHANGELOG.md)。

本插件移植自 [Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)，其命令语义可参考 [Diving-Fish/mai-bot](https://github.com/Diving-Fish/mai-bot)。当前主要服务国服环境，绘图资源按国服 PRiSM PLUS 资源结构适配。

## 功能特性

- 查询歌曲信息、定数、BPM、曲师、谱师
- 查询玩家成绩、Best 50、完成表、牌子进度
- 查询定数表、完成表、分数列表、上分推荐
- 猜歌游戏
- 机厅排卡
- 别名管理、投票与推送
- 查分器排行榜查询
- `mai识分`：机台照片、公众号成绩详情和 ADX 结算截图识别，匹配歌曲 ID，校验判定并计算绝赞细分

### 图片识分

发送 `mai识分` 并附上一张图片，或回复图片消息发送 `mai识分`。

识别完整时返回图片，展示封面、歌曲信息、达成率、DX 星级、判定分布、每格失分和绝赞细分。识别不完整时返回可编辑的补全指令：保留已识别的数据，用 `?` 表示缺失项。修改后直接发送同一条指令即可生成结果图片，无需再次附图。

例如（各行依次填写 CP / P / Great / Good / Miss）：

```text
mai识分 id=11167 ach=100.2098 tap=453/226/17/0/1 hold=28/12/0/0/0 slide=93/0/0/1/0 touch=19/0/0/0/0 break=18/19/1/0/0
```

通常会根据物量匹配难度，必要时可加 `diff=紫`；也可填写 `dx=2365/2664` 校验 DX 分。合并 CP 的判定表使用 P / Great / Good / Miss 四项；由 ADX 图片生成的补全指令会保留 `round=1`，沿用其四舍五入规则。

QQ 官方机器人优先以 Markdown 指令框和“填写识分信息”按钮回复。按钮将指令填入输入框，用户修改后发送；需要当前机器人具有原生 Markdown/按钮发送权限，接口不可用时自动回退为可复制的纯文本。

详细结算需要保留歌曲名、难度、达成率和完整判定表。识别结果包含歌曲 ID、各键型判定、绝赞小 P 的 50 落与 100 落，以及 Great 的 2000 / 1500 / 1250 三档。程序校验谱面物量、DX 分和达成率：机台/公众号按显示精度截断，ADX 按显示精度四舍五入，计算全程使用精确分数。缺失数字只在谱面物量能确定该数字时恢复；存在多个合法解时显示候选范围。

ADX 简略结算可以识别歌曲、达成率与汇总判定；计算绝赞细分请发送 `Details` 详细页，也可使用返回的指令补齐各键型判定。

使用本地 RapidOCR，图片不上传到识别服务。首次使用会下载 OCR 模型到 AstrBot 的 `data/plugin_data/astrbot_plugin_maibot/ocr/`；后续使用缓存。表格识别方案参考 [maimai-score-studio](https://github.com/Koishi0425/maimai-score-studio)。

舞萌 DX 的普通键型基础分为 TAP / HOLD / SLIDE / TOUCH = 500 / 1000 / 1500 / 500，Great 为 80%、Good 为 50%。绝赞基础分为 CP/P 2500、Great 2000/1500/1250、Good 1000；追加分为 CP 100、P50 75、P100 50、Great 40、Good 30。DX 分为 CP 3、P 2、Great 1、Good/Miss 0。规则参照 [MaiMuriDX 开发笔记](https://github.com/Minepig/MaiMuriDX/blob/master/docs/开发笔记.md)。

## 安装

### 1. 克隆项目

```bash
git clone https://github.com/Koishi0425/astrbot_plugin_maibot.git
```

将插件目录放到 AstrBot 的插件目录下。

### 2. 安装依赖

AstrBot 不会自动安装插件依赖，需要在 AstrBot 使用的 Python 环境中手动安装：

```bash
cd astrbot_plugin_maibot
pip install -r requirements.txt
```

安装 Chromium，用于部分图片生成：

```bash
python -m playwright install --with-deps chromium
```

Windows 下建议使用 `python -m playwright`，不要直接调用 `playwright`。

Linux 如缺少中文字体，可安装：

```bash
apt install fonts-wqy-microhei
```

## 配置

插件配置通过 AstrBot WebUI 管理。

WebUI 配置项：

- `bot_name`: 机器人名称，默认 `Bot`
- `enable_reply`: 是否在多数查询结果前添加引用回复
- `saveinmem`: 是否缓存图片资源到内存，关闭后更省内存但图片生成会稍慢
- `divingfish_client_id`: 部署者自己的水鱼 OAuth 机密客户端应用标识
- `divingfish_client_secret`: 对应应用凭据，仅保存在 WebUI 配置中
- `maimaidxproberproxy`: 是否代理公开曲库、排行榜及公开 B50 查询；OAuth 成绩查询始终直连官方
- `maimaidxaliaspush`: 是否启用别名投票 SSE 推送，默认关闭
- `maimaidxaliasvoting`: 是否允许远程别名申请、查看投票和参与投票，默认关闭
- `maimaidxaliasproxy`: 是否通过代理访问别名服务
- `maimaidxaliaswhitelist`: 别名推送白名单模式
- `arcade_enabled`: 是否启用机厅排卡及其数据更新，默认关闭
- `resource_local_path`: 本地资源目录或 `.7z` 文件路径，仅作为资源更新来源
- `resource_source_url`: 本地资源不可用时使用的资源包 URL，可留空
- `resource_check_on_startup`: 启动时检查关键静态资源是否缺失

超级管理员仍使用 AstrBot 主配置中的 `admins_id`，不在插件配置里设置。

### 水鱼 OAuth 配置与迁移

按照[官方迁移说明](https://maimai.diving-fish.com/manual/docs/developer/oauth-migration/)，旧 Developer-Token 及 `/dev/player/records`、`/dev/player/record`、`/query/plate` 将于 **2027 年 1 月 1 日 00:00（UTC+8）** 停用。本插件已改用 OAuth 的 `/player/records`、`/player/record`、`/player/plate`。

1. 部署者登录[水鱼开发者控制台](https://auth.diving-fish.com/console)，优先完善旧 Token 对应的迁移应用；新部署者登记自己的应用。
2. 选择“由开发者自己部署运行”的机密客户端和设备码接入，申请 `prober.records.read`，启用设备码和换票方式，补全应用名称等资料。
3. 在控制台生成 `client_secret`，把 `client_id`、`client_secret` 填入本插件的 WebUI 配置。每位部署者使用自己的应用凭据，不在插件源码中分发共用密钥。当前不支持分发共用 Client ID 的公开客户端模式。
4. 新用户发送 `mai授权`，核对消息和水鱼页面上的用户码、遮挡后的 QQ 身份后确认授权，再发送原查分指令；不需要公网回调地址。非 QQ 平台先使用现有的 `绑定QQ`。

**存量用户通常无需重新授权**：官方已迁移旧 Token 在过去 90 天内实际查询过的用户授权及标识映射。继续使用同一迁移应用后，插件通过 `sha256(client_id + ":" + 原QQ标识)` 换票，保留原映射；只有新用户、未被迁移或已撤销授权的用户才会收到 `mai授权` 提示。更换应用不会继承另一个应用的用户许可。按用户名查询其他玩家也要求该玩家已授权当前应用。

公开 B50、曲库、谱面统计及 `mai识分` 不依赖 OAuth。完整成绩、单曲详情、B40/AP50、完成表、牌子进度及单曲群排行需要授权；群排行只统计能够查询到的成员。访问令牌仅缓存在内存中，按有效期复用；失效后重新换票，不保留用户刷新令牌，也不申请成绩写入权限。用户可在水鱼账号设置中撤销许可；短期换票令牌按官方规则在到期后失效。

接口和设备码行为详见[官方 OAuth API 文档](https://maimai.diving-fish.com/manual/docs/developer/oauth-api-document/)。

## 静态资源

运行时图片资源读取插件目录下的 `static/`。资源包不会随插件发布，部署者需要自行准备资源并导入到 `static/`。

静态资源请参考项目[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)中作者发布的资源包。(以下链接截止至2026-06-30更新)
   - [Cloudreve私人云盘](https://cloud.yuzuchan.moe/f/34s7/Resource%20CN1.55.7z)
   - [onedrive](https://yuzuai-my.sharepoint.com/:u:/g/personal/yuzu_yuzuchan_moe/IQBGKHie6MAaTZy3rME7Q-ruAVKgXDCKROqz5e25KtMeeVY?e=53eC6a)
   - [openlist](https://share.yuzuchan.moe/d/downloads/Resource%20CN1.55.7z?sign=4wMRn_9n6YZiEVV2vELKCEOj9zsgxScnmgtjsEL3C6g=:0)

推荐流程：

1. 在 WebUI 中配置 `resource_local_path`，指向本地资源包、解压后的资源目录或 `.7z` 文件。
2. 由超级管理员执行 `更新舞萌资源`。或者直接进入目录解压。
3. 资源安装完成后执行或确认已执行 `更新定数表`、`更新完成表`。

资源更新规则：

- `resource_local_path` 优先级高于 `resource_source_url`。
- 缺少绘图素材不会中断插件初始化，曲目查询和数据更新仍可使用；相关绘图指令需要补齐资源。

两个更新入口的区别：

- `更新maimai数据`: 更新歌曲、牌子、别名、猜歌数据；如果未配置资源来源，会跳过静态资源。
- `更新舞萌资源`: 兼容旧资源更新入口，会执行同一套数据与资源更新流程，但要求配置了本地资源路径或资源 URL。

## 常用命令

### 基础查询

- `mai帮助`: 查看插件帮助
- `查歌 <关键词>` / `search <关键词>`: 搜索歌曲
- `定数查歌 <定数>`: 按定数搜索
- `bpm查歌 <bpm>`: 按 BPM 搜索
- `曲师查歌 <曲师名>`: 按曲师搜索
- `谱师查歌 <谱师名>`: 按谱师搜索
- `版本查歌 <版本名/牌子字/发布日期前缀> [页数]`: 按版本或发布日期搜索
- `id <歌曲id>`: 查询指定歌曲信息
- `minfo <歌曲id或别名>` / `info <歌曲id或别名>`: 查询个人游玩详情
- `ginfo <歌曲id或别名> [绿/黄/红/紫/白]`: 查询歌曲游玩分布，例如 `ginfo 799 紫`
- `是什么歌 <别名>`: 通过别名查询歌曲

### 成绩查询

- `绑定QQ <QQ号>`: 绑定当前平台用户到 QQ 号
- `查看QQ绑定`: 查看当前绑定的 QQ 号
- `解绑QQ`: 解除当前 QQ 绑定
- `mai授权`: 新用户或授权失效用户为当前水鱼应用授权成绩读取；已迁移的存量用户无需重复操作
- `b50 <QQ号或查分器用户名>`: 查询 Best 50
- `b40 <QQ号或查分器用户名>`: 从全谱面成绩中按单谱 Rating 生成前 40
- `ap50 <QQ号或查分器用户名>`: 从 AP/AP+ 成绩中按单谱 Rating 生成前 50
- `pk50 <QQ号或查分器用户名>`: 将自己的 B50 与另一名玩家比较
- `谱面统计 <歌曲/ID> [难度]`: 查看 FC率、AP率、平均成绩和本曲难度样本占比
- `分数线 <歌曲名/别名/ID> [难度]`: 生成判定失分、评级容错与 DX 星级解析图，难度默认紫谱 Master
- `<版本><目标>进度`: 查询牌子进度，例如 `晓将进度`
- `<等级><目标>进度`: 查询等级进度，例如 `13sss进度`
- `<等级或定数>分数列表`: 查询成绩列表
- `分类进度 ...`: 按版本、分类、等级、定数、难度、DX/SD、目标和状态组合查询；发送 `分类进度 帮助` 查看格式
- `段位表 [段位]`: 查看 PRiSM PLUS 初段～十段、真段位、真皆传及裏皆传的四首课题曲
- `我要在 <等级> 上 <分数> 分`: 查询上分推荐
- `查看排名`: 查看查分器排行榜
- `群rating [页数]`: 查看本群 Rating 排行，每页最多 10 人
- `加入群榜` / `退出群榜`: 手动加入或退出本群 Rating 榜
- `群榜签名 <文本>` / `清除群榜签名`: 设置榜单名片签名，最多 32 个字符
- `grank <歌曲名/别名/ID> [难度]`: 查询指定谱面的群内成绩排行，难度默认紫谱
- `群排行 <歌曲名/别名/ID> [难度]`: `grank` 的中文写法

在群内查询自己的 `b50` 会自动加入该群榜单。群榜使用 QQ 头像、舞萌昵称、水鱼 ID、Rating、B35/B15 和个性签名展示；请求者不在当前页时，页面底部仍会显示其总榜名次。

单曲群排行难度支持 `绿/黄/红/紫/白` 或 `Basic/Advanced/Expert/Master/Re:Master`。例如：`grank 799`、`群排行 潘多拉 白`。超过 10 人时可在末尾使用 `第2页` 或 `p2` 翻页。

### 表格查询

- `定数表 <等级>`: 查看定数表
- `<等级><目标>完成表 [QQ号或查分器用户名]`: 查看等级完成表，例如 `13+ap完成表 username`
- `<版本><目标>完成表 [QQ号或查分器用户名]`: 查看牌子完成表，例如 `晓将完成表 username`；也支持 @ 用户
- `舞/霸` 完成表使用五难度版式；没有 Re:Master 的旧曲只要求其实际存在的四个难度

### 猜歌游戏

- `猜歌`: 开始猜歌
- `猜歌提示`: 获取提示
- `猜歌重置`: 重置游戏

### 机厅功能

机厅排卡默认关闭，需要先在 AstrBot WebUI 中开启 `arcade_enabled`。

- `帮助maimaiDX排卡`: 查看机厅帮助
- `添加机厅 <店名> <地址> <id>`: 添加机厅
- `查找机厅 <关键词>`: 查找机厅
- `订阅机厅 <店名>`: 订阅机厅
- `机厅几人`: 查看已订阅机厅排卡人数

### 别名管理

别名查询、本地别名和别名库更新始终可用；远程申请/投票与推送默认关闭，需要分别开启 `maimaidxaliasvoting`、`maimaidxaliaspush`。

- `更新别名库`: 手动更新别名库
- `添加别名 <歌曲id> <别名>`: 添加别名
- `当前投票`: 查看当前别名投票
- `开启别名推送` / `关闭别名推送`: 开启或关闭本群别名推送
- `全局开启别名推送` / `全局关闭别名推送`: 超级管理员全局控制别名推送

### 管理命令

以下命令需要超级管理员权限：

- `更新maimai数据`: 更新运行时数据，资源来源已配置时同步安装静态资源
- `更新舞萌资源`: 要求资源来源已配置，执行数据与静态资源完整更新
- `更新maimai资源` / `更新maimaiDX资源` / `更新maimaidx资源`: `更新舞萌资源` 的兼容别名
- `更新定数表`: 重新生成插件数据目录中的 `rating_table/`
- `更新完成表`: 重新生成插件数据目录中的 `plate_table/`
- `更新别名库`: 更新别名库

## 首次使用建议

1. 安装依赖并重启 AstrBot。
2. 在 WebUI 中配置查分器、别名、资源相关选项。
3. 准备静态资源，并解压到 `static/` 目录。
4. 执行 `更新定数表` 和 `更新完成表`。
5. 使用 `b50`、`info`、`xx完成表` 等命令验证图片资源是否完整。

## 迁移说明

- 命令已迁移到 AstrBot 框架。
- 使用 AstrBot 的 `admins_id` 权限管理。
- 配置入口迁移到 AstrBot WebUI。
- `static/config.json` 已废弃。
- QQ 绑定、群设置、机厅排卡、曲库/别名缓存及生成表保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_maibot/`。升级时会自动复制旧 `static/` 中的 JSON 数据；已有的新目录数据不会被覆盖。静态绘图资源仍从插件 `static/` 读取。
- 网络超时、连接失败或服务异常时使用已有曲库、谱面统计、别名与牌子缓存；没有谱面统计时仍可查询基础曲目信息。
- 用户 JSON 数据通过同目录临时文件原子替换保存。损坏文件会保留原文件和 `*.corrupt-*.bak` 备份；恢复原文件后重载插件。群开关损坏时暂停群内功能和别名推送，避免原本关闭的群被误开启。

## 注意事项

- `resource_source_url` 可以留空；没有稳定资源链接时推荐使用本地资源包导入。
- `更新maimai数据` 里的别名数据依赖外部服务，网络不可达时需要检查代理或稍后重试。
- `saveinmem=false` 可降低内存占用，但图片生成会稍慢。
- 如果替换了 `static/` 资源，建议重载插件或执行更新命令刷新图片缓存。
- 水鱼公开 `chart_stats` 不提供全服总玩家数，因此 `谱面统计` 的“游玩率”使用同一首歌各难度统计样本的占比，不冒充全服游玩率。
- 段位目录保存在 `static/dan_courses_cn_prism_plus.json`，课题曲依据 PRiSM PLUS 资料整理；国服发生本地化调整时应直接更新该数据文件。

## 许可证

MIT License

## 致谢

- 参考项目：[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
- 查分器参考：[Diving-Fish/mai-bot](https://github.com/Diving-Fish/mai-bot)
- 框架：[AstrBot](https://astrbot.app)

## 支持

如有问题，请提交 Issue 或查看 [AstrBot 帮助文档](https://astrbot.app)。
