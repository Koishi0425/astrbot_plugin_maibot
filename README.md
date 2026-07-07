# astrbot_plugin_maimaidx

[![python3](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

基于 [AstrBot](https://astrbot.app) 框架的街机音游 **舞萌DX** 查分插件

移植自 [maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) 项目（原基于 HoshinoBot/NoneBot）

## 功能特性

- 🎵 查询歌曲信息、定数、BPM、曲师、谱师
- 📊 查询玩家成绩、Best 50、牌子进度
- 🎮 猜歌游戏功能
- 🏪 机厅排卡功能
- 🏷️ 别名管理和推送
- 📈 定数表和完成表查询
- 📋 排行榜查询

## 安装方法

### 1. 克隆项目

```bash
git clone https://github.com/ZhiheZier/astrbot_plugin_maimaidx.git
```

### 2. 下载静态资源

舞萌绘图运行时读取插件目录下的 `static/`。推荐在 AstrBot WebUI 插件配置中设置 `resource_local_path`（可选的本地资源包/目录）或 `resource_source_url` 后，由超级管理员在聊天中执行以下指令安装静态资源：

```text
更新maimai数据
或
更新舞萌资源
```

`更新maimai数据` 与 `更新舞萌资源` 现在使用同一套完整更新流程：先刷新歌曲、牌子、别名与猜歌数据，再把资源安装或覆盖到 `static/`。如果配置了本地资源目录或 `.7z` 资源包，会优先使用本地来源；本地来源未配置或不可用时，才会使用 WebUI 中配置的资源 URL。配置全部由 AstrBot WebUI 管理，不再读取或写入 `static/config.json`。

本地资源可以是全量包、解压后的资源目录，或版本增量包。CN1.56 的增量包会被识别为 rating 数字素材并覆盖到 `static/mai/pic`。

### 3. 安装依赖

**重要：AstrBot 不会自动安装插件依赖，需要手动安装。**

安装 Python 依赖：

```bash
cd astrbot_plugin_maimaidx
pip install -r requirements.txt
```

安装 Chromium（用于图片生成）：

```bash
python -m playwright install --with-deps chromium
```

**注意**：在 Windows 上需要使用 `python -m playwright` 而不是直接使用 `playwright` 命令。

安装字体（Linux 系统，Windows 可跳过）：

```bash
apt install fonts-wqy-microhei
```

### 4. 配置插件

#### 4.1 插件配置（通过 AstrBot 配置界面）

在 AstrBot 的插件配置界面中，可以配置以下选项：

- `bot_name`: 机器人名称，用于在消息中显示（如：今日运势功能），默认为 "Bot"
- `enable_reply`: 是否在多数指令回复中添加“引用消息”（Reply），默认为开启
- `maimaidxtoken`: 查分器开发者 token（建议配置；目前牌子进度等功能需要 token）
- `maimaidxproberproxy`: 是否使用代理访问查分器 API
- `maimaidxaliasproxy`: 是否使用代理访问别名库 API
- `maimaidxaliaspush`: 是否开启别名推送
- `maimaidxaliaswhitelist`: 别名推送是否采用白名单（默认关闭）。开启后仅向已执行「开启别名推送」的群广播，可避免多群刷屏；关闭时为「全群推送 + disable 黑名单」
- `saveinmem`: 是否将图片保存在内存中（`false` 可节省内存）
- `resource_local_path`: 可选的本地资源目录或 `.7z` 文件路径，仅作为更新来源；安装目标始终是 `static/`
- `resource_source_url`: 本地资源不存在时使用的资源包 URL，可留空
- `resource_check_on_startup`: 启动时检查关键资源是否缺失

### 5. 配置超级管理员

在 AstrBot 主配置文件中设置管理员ID列表（字段名为 `admins_id`），用于执行更新数据等管理命令。

**注意**：管理员ID配置在 AstrBot 的主配置文件中，不在插件配置中。

### 6. 启用插件

将插件目录放置在 AstrBot 的插件目录下，重启 AstrBot 即可。

## 主要命令

### 基础查询
- `查歌 <关键词>` / `search <关键词>` - 搜索歌曲
- `定数查歌 <定数>` - 按定数搜索
- `bpm查歌 <bpm>` - 按 BPM 搜索
- `曲师查歌 <曲师名>` - 按曲师搜索
- `谱师查歌 <谱师名>` - 按谱师搜索
- `id <歌曲id>` - 查询指定歌曲信息
- `是什么歌 <别名>` - 通过别名查询歌曲

### 成绩查询
- `绑定QQ <QQ号>` - 绑定当前平台用户ID到 QQ 号（官方 QQ 机器人无法直接获取 QQ 号时使用）
- `查看QQ绑定` - 查看当前绑定的 QQ 号
- `解绑QQ` - 解除当前 QQ 绑定
- `b50 <QQ号或查分器用户名>` - 查询 Best 50
- `分数线 <难度+id> <分数>` - 查询分数线
- `牌子进度 <QQ号>` - 查询牌子进度
- `查看排名` - 查看排行榜

### 猜歌游戏
- `猜歌` - 开始猜歌
- `猜歌提示` - 获取提示
- `猜歌重置` - 重置游戏

### 机厅功能
- `帮助maimaiDX排卡` - 查看机厅帮助
- `添加机厅 <店名> <地址> <id>` - 添加机厅
- `查找机厅 <关键词>` - 查找机厅
- `订阅机厅 <店名>` - 订阅机厅
- `机厅几人` - 查看已订阅机厅排卡人数

### 别名管理
- `添加别名 <歌曲id> <别名>` - 添加别名
- `当前投票` - 查看当前别名投票
- `开启别名推送` / `关闭别名推送` - 开启/关闭别名推送

### 管理命令（需要超级管理员权限）
- `更新maimai数据` - 更新歌曲/牌子/别名数据，并在配置了资源来源时同步更新静态资源
- `更新舞萌资源` - 兼容旧资源更新入口，同样执行完整更新；别名：`更新maimai资源`、`更新maimaiDX资源`、`更新maimaidx资源`
- `更新定数表` - 更新定数表
- `更新完成表` - 更新完成表
- `更新别名库` - 更新别名库

## 迁移说明

本插件从 HoshinoBot/NoneBot 框架迁移到 AstrBot 框架。

### 主要变更

- ✅ 所有命令已迁移到 astrbot 框架
- ✅ 移除了对 `hoshino` 包的依赖
- ✅ 使用 astrbot 的权限管理系统（`admins_id`）
- ✅ 支持主动消息发送（猜歌提示、别名推送等）
- ✅ 支持手动配置 bot 名称（通过插件配置界面）

## 注意事项

1. **首次使用**：首次使用需要执行 `更新定数表` 和 `更新完成表` 命令生成相关数据
2. **资源文件**：必须下载并配置静态资源文件，否则部分功能无法使用
3. **别名推送**：如果关闭别名推送，将不会实时更新别名库
4. **内存配置**：`saveinmem` 设置为 `false` 可节省内存，但 Best 50 图片生成会稍慢

## 许可证

MIT License

## 致谢

- 原项目：[maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
- 查分器：[mai-bot](https://github.com/Diving-Fish/mai-bot)
- 框架：[AstrBot](https://astrbot.app)

## 支持

如有问题，请提交 Issue 或查看 [AstrBot 帮助文档](https://astrbot.app)
