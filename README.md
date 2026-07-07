# astrbot_plugin_maimaidx

[![python3](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

基于 [AstrBot](https://astrbot.app) 框架的街机音游 **舞萌DX** 查分插件。

本插件移植自 [Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)，其命令语义可参考 [Diving-Fish/mai-bot](https://github.com/Diving-Fish/mai-bot)。当前主要服务国服环境，绘图资源按国服 PRiSM PLUS 资源结构适配。

## 功能特性

- 查询歌曲信息、定数、BPM、曲师、谱师
- 查询玩家成绩、Best 50、完成表、牌子进度
- 查询定数表、完成表、分数列表、上分推荐
- 猜歌游戏
- 机厅排卡
- 别名管理、投票与推送
- 查分器排行榜查询

## 安装

### 1. 克隆项目

```bash
git clone https://github.com/ZhiheZier/astrbot_plugin_maimaidx.git
```

将插件目录放到 AstrBot 的插件目录下。

### 2. 安装依赖

AstrBot 不会自动安装插件依赖，需要在 AstrBot 使用的 Python 环境中手动安装：

```bash
cd astrbot_plugin_maimaidx
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
- `maimaidxtoken`: Diving-Fish 查分器开发者 token，部分查分/进度接口建议配置
- `maimaidxproberproxy`: 是否通过代理访问查分器 API
- `maimaidxaliaspush`: 是否启用别名推送 WebSocket
- `maimaidxaliasproxy`: 是否通过代理访问别名服务
- `maimaidxaliaswhitelist`: 别名推送白名单模式
- `resource_local_path`: 本地资源目录或 `.7z` 文件路径，仅作为资源更新来源
- `resource_source_url`: 本地资源不可用时使用的资源包 URL，可留空
- `resource_check_on_startup`: 启动时检查关键静态资源是否缺失

超级管理员仍使用 AstrBot 主配置中的 `admins_id`，不在插件配置里设置。

## 静态资源

运行时图片资源读取插件目录下的 `static/`。资源包不会随插件发布，部署者需要自行准备资源并导入到 `static/`。

静态资源请参考项目[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)中作者发布的资源包。(以下链接截止至2026-06-30更新)
   - [Cloudreve私人云盘](https://cloud.yuzuchan.moe/f/34s7/Resource%20CN1.55.7z)
   - [onedrive](https://yuzuai-my.sharepoint.com/:u:/g/personal/yuzu_yuzuchan_moe/IQBGKHie6MAaTZy3rME7Q-ruAVKgXDCKROqz5e25KtMeeVY?e=53eC6a)
   - [openlist](https://share.yuzuchan.moe/d/downloads/Resource%20CN1.55.7z?sign=4wMRn_9n6YZiEVV2vELKCEOj9zsgxScnmgtjsEL3C6g=:0)

推荐流程：

1. 在 WebUI 中配置 `resource_local_path`，指向本地资源包、解压后的资源目录或 `.7z` 文件。
2. 由超级管理员执行 `更新舞萌资源`。
3. 资源安装完成后执行或确认已执行 `更新定数表`、`更新完成表`。

资源更新规则：

- `resource_local_path` 优先级高于 `resource_source_url`。
- 当前新版绘图不兼容旧版资源缺失素材的情况；如果日志提示缺少关键文件，请更新静态资源。

两个更新入口的区别：

- `更新maimai数据`: 更新歌曲、牌子、别名、猜歌数据；如果未配置资源来源，会跳过静态资源。
- `更新舞萌资源`: 兼容旧资源更新入口，会执行同一套数据与资源更新流程，但要求配置了本地资源路径或资源 URL。

## 常用命令

### 基础查询

- `查歌 <关键词>` / `search <关键词>`: 搜索歌曲
- `定数查歌 <定数>`: 按定数搜索
- `bpm查歌 <bpm>`: 按 BPM 搜索
- `曲师查歌 <曲师名>`: 按曲师搜索
- `谱师查歌 <谱师名>`: 按谱师搜索
- `id <歌曲id>`: 查询指定歌曲信息
- `minfo <歌曲id或别名>` / `info <歌曲id或别名>`: 查询个人游玩详情
- `ginfo <歌曲id或别名>`: 查询歌曲游玩分布
- `是什么歌 <别名>`: 通过别名查询歌曲

### 成绩查询

- `绑定QQ <QQ号>`: 绑定当前平台用户到 QQ 号
- `查看QQ绑定`: 查看当前绑定的 QQ 号
- `解绑QQ`: 解除当前 QQ 绑定
- `b50 <QQ号或查分器用户名>`: 查询 Best 50
- `分数线 <难度+id> <分数>`: 查询分数线
- `<版本><目标>进度`: 查询牌子进度，例如 `晓将进度`
- `<等级> <目标>进度`: 查询等级进度，例如 `13 sss进度`
- `<等级或定数>分数列表`: 查询成绩列表
- `我要在 <等级> 上 <分数> 分`: 查询上分推荐
- `查看排名`: 查看查分器排行榜

### 表格查询

- `定数表 <等级>`: 查看定数表
- `<等级><目标>完成表`: 查看等级完成表，例如 `13+sss完成表`
- `<版本><目标>完成表`: 查看牌子完成表，例如 `晓将完成表`

### 猜歌游戏

- `猜歌`: 开始猜歌
- `猜歌提示`: 获取提示
- `猜歌重置`: 重置游戏

### 机厅功能

- `帮助maimaiDX排卡`: 查看机厅帮助
- `添加机厅 <店名> <地址> <id>`: 添加机厅
- `查找机厅 <关键词>`: 查找机厅
- `订阅机厅 <店名>`: 订阅机厅
- `机厅几人`: 查看已订阅机厅排卡人数

### 别名管理

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
- `更新定数表`: 重新生成 `static/mai/rating_table`
- `更新完成表`: 重新生成 `static/mai/plate_table`
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

## 注意事项

- `resource_source_url` 可以留空；没有稳定资源链接时推荐使用本地资源包导入。
- `更新maimai数据` 里的别名数据依赖外部服务，网络不可达时需要检查代理或稍后重试。
- `saveinmem=false` 可降低内存占用，但图片生成会稍慢。
- 如果替换了 `static/` 资源，建议重载插件或执行更新命令刷新图片缓存。

## 许可证

MIT License

## 致谢

- 参考项目：[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
- 查分器参考：[Diving-Fish/mai-bot](https://github.com/Diving-Fish/mai-bot)
- 框架：[AstrBot](https://astrbot.app)

## 支持

如有问题，请提交 Issue 或查看 [AstrBot 帮助文档](https://astrbot.app)。
