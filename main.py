from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
import traceback
import json
import re
from pathlib import Path

from . import Root, log, loga, ratingdir, platedir, plate_to_dx_version, platecn, static, _BOTNAME
from .libraries.maimai_best_50 import ScoreBaseImage
from .libraries.maimaidx_api_data import MaiConfig, maiApi
from .libraries.maimaidx_music import mai
from .command.mai_alias import ws_alias_server
import sys


def _config_get(config: dict, key: str, default=None):
    if key in config:
        return config.get(key, default)
    for section in ("basic", "prober", "alias", "arcade", "resource", "sdgb", "tech"):
        section_config = config.get(section)
        if isinstance(section_config, dict) and key in section_config:
            return section_config.get(key, default)
    return default


def _config_bool(config: dict, key: str, default: bool) -> bool:
    value = _config_get(config, key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off", "关闭", "否"}:
            return False
        if normalized in {"1", "true", "yes", "on", "开启", "是"}:
            return True
    return bool(value)


@register("astrbot_plugin_maibot", "ZhiheZier", "maimaiDX插件", "1.4.0")
class MaimaiDXPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        from .libraries.sdgbtech_bridge import install_qr_log_redaction
        install_qr_log_redaction()
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        
        # 群组启用状态（存储禁用的群组ID）
        self.disabled_groups = set()  # 禁用插件的群组ID集合
        self.data_file = static / "disabled_groups.json"  # 数据文件路径
        
        # 从插件配置中读取 bot 名称并设置到 __init__.py
        bot_name = _config_get(self.config, "bot_name", "Bot")
        enable_reply = _config_bool(self.config, "enable_reply", True)
        maiApi.configure(MaiConfig(
            maimaidxtoken=str(_config_get(self.config, "maimaidxtoken", "") or "").strip() or None,
            maimaidxproberproxy=_config_bool(self.config, "maimaidxproberproxy", False),
            maimaidxaliasproxy=_config_bool(self.config, "maimaidxaliasproxy", False),
            maimaidxaliaspush=_config_bool(self.config, "maimaidxaliaspush", False),
            maimaidxaliasvoting=_config_bool(self.config, "maimaidxaliasvoting", False),
            maimaidxaliaswhitelist=_config_bool(self.config, "maimaidxaliaswhitelist", False),
            saveinmem=_config_bool(self.config, "saveinmem", True),
            resource_local_path=str(_config_get(self.config, "resource_local_path", "") or "").strip(),
            resource_source_url=str(_config_get(self.config, "resource_source_url", "") or "").strip(),
            resource_check_on_startup=_config_bool(self.config, "resource_check_on_startup", True),
            arcade_enabled=_config_bool(self.config, "arcade_enabled", False),
        ))
        pkg_name = __name__.rsplit('.', 1)[0]  # 获取包名，例如 'myplugins.astrbot_plugin_maimaidx'
        if pkg_name in sys.modules:
            module = sys.modules[pkg_name]
            # 更新内部变量和公共变量
            setattr(module, '_BOTNAME', bot_name)
            setattr(module, 'BOTNAME', bot_name)
            setattr(module, '_ENABLE_REPLY', enable_reply)
            log.info(f'已设置 bot 名称: {bot_name}')
            log.info(f'引用回复（Reply）: {"开启" if enable_reply else "关闭"}')
        
        # 从 astrbot 配置文件中获取管理员ID列表
        # 根据文档：https://docs.astrbot.app/dev/star/plugin.html
        # 使用 context.get_config() 获取配置，字段名为 admins_id
        bot_config = context.get_config()
        admins = bot_config.get("admins_id", [])
        # 确保所有ID都是字符串格式
        self.superusers = [str(admin) for admin in admins] if admins else []
        
        if self.superusers:
            log.info(f'从 astrbot 配置中获取到管理员ID列表: {self.superusers}')
        else:
            log.warning('未找到任何管理员ID，某些需要管理员权限的命令可能无法使用')

    async def initialize(self):
        """插件初始化，加载数据并设置定时任务"""
        try:
            # 加载禁用群组列表
            self._load_disabled_groups()
            
            # 设置配置
            self._setup_configuration()
            
            # 加载maimai数据
            await self._load_maimai_data()
            
            # 加载机厅数据
            if maiApi.config.arcade_enabled:
                await self._load_arcade_data()
            else:
                loga.info('机厅排卡功能未启用，跳过机厅数据初始化')
            
            # 加载图片到内存（如果配置了）
            self._load_images_to_memory()
            
            # 执行初始检查
            self._perform_initial_checks()
            
            # 设置定时任务
            self._setup_schedulers()
            
            log.info('maimaiDX插件初始化完成')
            log.info('命令注册完成，等待用户输入...')
        except Exception as e:
            log.error(f'插件初始化失败: {e}')
            log.error(traceback.format_exc())
            log.warning('初始化失败，但继续加载插件，部分功能可能不可用')

    def _load_disabled_groups(self):
        """加载禁用群组列表"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.disabled_groups = set(data.get('disabled_groups', []))
                log.info(f'已加载禁用群组列表，共 {len(self.disabled_groups)} 个群组')
            else:
                log.info('禁用群组列表文件不存在，创建新文件')
                self._save_disabled_groups()
        except Exception as e:
            log.error(f'加载禁用群组列表失败: {e}')
            self.disabled_groups = set()
    
    def _save_disabled_groups(self):
        """保存禁用群组列表"""
        try:
            data = {'disabled_groups': list(self.disabled_groups)}
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info('禁用群组列表已保存')
        except Exception as e:
            log.error(f'保存禁用群组列表失败: {e}')
    
    def _is_group_enabled(self, group_id: str) -> bool:
        """检查群组是否启用插件"""
        return group_id not in self.disabled_groups
    
    def _setup_configuration(self):
        """处理配置相关的初始化"""
        if maiApi.config.maimaidxproberproxy:
            log.info('正在使用代理服务器访问查分器')
        if maiApi.config.maimaidxaliasproxy:
            log.info('正在使用代理服务器访问别名服务器')
        maiApi.load_token_proxy()
        
        if maiApi.config.maimaidxaliaspush:
            log.info('别名推送为「开启」状态')
            # 启动别名推送 WebSocket 服务器
            asyncio.ensure_future(ws_alias_server(self.context))
        else:
            log.info('别名推送为「关闭」状态')
        log.info(f'别名申请与投票为「{"开启" if maiApi.config.maimaidxaliasvoting else "关闭"}」状态')
        loga.info(f'机厅排卡为「{"开启" if maiApi.config.arcade_enabled else "关闭"}」状态')

    async def _load_maimai_data(self):
        """负责加载所有maimai相关数据（歌曲、别名等）"""
        try:
            log.info('正在获取maimai所有曲目信息')
            await mai.get_music()
            log.info(f'歌曲数据获取完成，数量: {len(mai.total_list) if hasattr(mai, "total_list") and mai.total_list else 0}')
            
            log.info('正在获取maimai牌子数据')
            await mai.get_plate_json()
            log.info('牌子数据获取完成')
            
            log.info('正在获取maimai所有曲目别名信息')
            await mai.get_music_alias()
            log.info(f'别名数据获取完成，数量: {len(mai.total_alias_list) if hasattr(mai, "total_alias_list") and mai.total_alias_list else 0}')
            
            log.info('正在初始化猜歌数据')
            mai.guess()
            log.info('猜歌数据初始化完成')
            
            log.info('maimai数据获取完成')
        except Exception as e:
            log.error(f'加载maimai数据失败: {e}')
            log.error(traceback.format_exc())
            raise

    async def _load_arcade_data(self):
        """加载机厅数据"""
        try:
            from .libraries.maimaidx_arcade import arcade
            loga.info('正在获取maimai所有机厅信息')
            await arcade.getArcade()
            loga.info('maimai机厅数据获取完成')
        except ImportError:
            loga.warning('机厅功能未迁移，跳过机厅数据初始化')
        except Exception as e:
            loga.error(f'机厅数据初始化失败: {e}')

    def _load_images_to_memory(self):
        """如果配置了，将图片加载到内存中"""
        if maiApi.config.saveinmem:
            ScoreBaseImage._load_image()
            log.info('已将图片保存在内存中')

    def _perform_initial_checks(self):
        """执行对目录和数据的初始检查"""
        # 检查定数表文件夹
        if maiApi.config.resource_check_on_startup:
            try:
                from .libraries.maimaidx_resource import check_resource_status

                resource_status = check_resource_status()
                for path in resource_status.missing_paths:
                    log.warning(f'舞萌资源缺失：{path}')
                for warning in resource_status.warnings:
                    log.warning(f'舞萌资源警告：{warning}')
            except Exception as e:
                log.warning(f'检查舞萌资源状态失败: {e}')

        if not ratingdir.exists() or not list(ratingdir.iterdir()):
            log.warning(
                '注意！注意！检测到定数表文件夹为空！'
                '可能导致「定数表」「完成表」指令无法使用，'
                '请及时私聊BOT使用指令「更新定数表」进行生成。'
            )
        
        # 检查完成表文件夹
        plate_list = [name for name in list(plate_to_dx_version.keys())[1:]]
        platedir_list = [f.name.split('.')[0] for f in platedir.iterdir()] if platedir.exists() else []
        cn_list = [name for name in list(platecn.keys())]
        notin = set(plate_list) - set(platedir_list) - set(cn_list)
        if notin:
            anyname = '，'.join(notin)
            log.warning(
                f'注意！注意！未检测到牌子文件夹中的牌子：{anyname}，'
                '可能导致这些牌子的「完成表」指令无法使用，'
                '请及时私聊BOT使用指令「更新完成表」进行生成。'
            )
        
        # 检查数据是否加载成功
        try:
            if hasattr(mai, 'total_list') and mai.total_list:
                log.info(f'歌曲数据数量: {len(mai.total_list)}')
            else:
                log.error('歌曲数据未加载！')
            if hasattr(mai, 'total_alias_list') and mai.total_alias_list:
                log.info(f'别名数据数量: {len(mai.total_alias_list)}')
            else:
                log.warning('别名数据未加载')
        except Exception as e:
            log.warning(f'检查数据状态时出错: {e}')
            log.error(traceback.format_exc())

    def _setup_schedulers(self):
        """专门用于设置所有 apscheduler 定时任务"""
        # 设置定时任务：每天凌晨4点更新数据
        self.scheduler.add_job(
            self._daily_update,
            trigger=CronTrigger(hour=4),
            name="maimai_daily_update",
            misfire_grace_time=300
        )
        
        # 添加机厅数据每日更新定时任务（每天凌晨3点）
        if not maiApi.config.arcade_enabled:
            return
        try:
            from .libraries.maimaidx_arcade import arcade
            self.scheduler.add_job(
                self._arcade_daily_update,
                trigger=CronTrigger(hour=3),
                name="arcade_daily_update",
                misfire_grace_time=300
            )
        except ImportError:
            # 机厅功能未迁移，跳过定时任务设置
            pass

    async def _daily_update(self):
        """定时任务：每日更新数据"""
        try:
            await mai.get_music()
            mai.guess()
            log.info('maimaiDX数据更新完毕')
        except Exception as e:
            log.error(f'定时更新数据失败: {e}')
    
    async def _arcade_daily_update(self):
        """机厅数据每日更新 - 每天凌晨3点执行"""
        try:
            from .libraries.maimaidx_arcade import arcade
            await arcade.getArcade()
            loga.info('maimai机厅数据更新完成')
        except Exception as e:
            loga.error(f'机厅数据更新失败: {e}')

    # 注册命令处理函数
    # 群组开关命令（超级管理员专用）
    @filter.regex(r'^(开启|关闭)舞萌功能$')
    async def toggle_maimai_feature(self, event: AstrMessageEvent):
        """开启/关闭舞萌功能（超级管理员专用）"""
        # 检查是否为超级管理员
        sender_id = str(event.get_sender_id())
        if sender_id not in self.superusers:
            yield event.plain_result('仅允许超级管理员执行此操作')
            return
        
        # 检查是否在群聊中
        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result('此命令仅在群聊中可用')
            return
        
        gid = str(group_id)
        message_str = event.message_str.strip()
        
        if message_str == '开启舞萌功能':
            if gid in self.disabled_groups:
                self.disabled_groups.remove(gid)
                self._save_disabled_groups()
                yield event.plain_result(f'已开启本群的舞萌功能')
            else:
                yield event.plain_result('本群舞萌功能已经是开启状态')
        elif message_str == '关闭舞萌功能':
            if gid not in self.disabled_groups:
                self.disabled_groups.add(gid)
                self._save_disabled_groups()
                yield event.plain_result(f'已关闭本群的舞萌功能')
            else:
                yield event.plain_result('本群舞萌功能已经是关闭状态')
    
    # 基础命令
    @filter.command("更新maimai数据")
    async def update_data(self, event: AstrMessageEvent):
        """更新maimai数据与静态资源"""
        from .command.mai_update import update_maimai_all_handler
        async for result in update_maimai_all_handler(event, self.superusers, maiApi.config):
            yield result

    @filter.regex(r'^(更新舞萌资源|更新maimai资源|更新maimaiDX资源|更新maimaidx资源)$')
    async def update_maimai_resources(self, event: AstrMessageEvent):
        """更新maimai数据与静态资源，兼容旧资源更新指令"""
        from .command.mai_update import update_maimai_all_handler
        async for result in update_maimai_all_handler(event, self.superusers, maiApi.config, require_resource_source=True):
            yield result

    @filter.regex(r'^(绑定QQ|绑定qq)\s*([1-9][0-9]{4,11})$')
    async def bind_qq(self, event: AstrMessageEvent):
        """绑定QQ号"""
        from .command.mai_bind import bind_qq_handler
        async for result in bind_qq_handler(event):
            yield result

    @filter.regex(r'^(查看QQ绑定|查看qq绑定)$')
    async def query_qq_bind(self, event: AstrMessageEvent):
        """查看QQ绑定"""
        from .command.mai_bind import query_qq_bind_handler
        async for result in query_qq_bind_handler(event):
            yield result

    @filter.regex(r'^(解绑QQ|解绑qq)$')
    async def unbind_qq(self, event: AstrMessageEvent):
        """解绑QQ号"""
        from .command.mai_bind import unbind_qq_handler
        async for result in unbind_qq_handler(event):
            yield result

    @filter.regex(r'^mai帮助$')
    async def maimaidxhelp(self, event: AstrMessageEvent):
        """mai帮助"""
        # 检查群组是否启用
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        
        from .command.mai_base import maimaidxhelp_handler
        async for result in maimaidxhelp_handler(event):
            yield result

    @filter.regex(r'^(今日mai|今日舞萌|今日运势)$')
    async def mai_today(self, event: AstrMessageEvent):
        """今日运势"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import mai_today_handler
        async for result in mai_today_handler(event):
            yield result

    @filter.regex(r'.*mai.*什么(.+)?')
    async def mai_what(self, event: AstrMessageEvent):
        """mai什么"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import mai_what_handler
        async for result in mai_what_handler(event):
            yield result

    @filter.regex(r'^[来随给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$')
    async def random_song(self, event: AstrMessageEvent):
        """随机歌曲"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import random_song_handler
        async for result in random_song_handler(event):
            yield result

    @filter.regex(r'^(查看排名|查看排行)$')
    async def rating_ranking(self, event: AstrMessageEvent):
        """查看排名"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import rating_ranking_handler
        async for result in rating_ranking_handler(event):
            yield result

    @filter.regex(r'^(我的排名)$')
    async def my_rating_ranking(self, event: AstrMessageEvent):
        """我的排名"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import my_rating_ranking_handler
        async for result in my_rating_ranking_handler(event):
            yield result

    # 成绩查询命令
    @filter.regex(r'^(b50|B50)\s*(.*)$')
    async def best50(self, event: AstrMessageEvent):
        """b50/B50 命令"""
        # 检查群组是否启用
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        if group_id and event.message_str.strip().lower() == 'b50':
            from .libraries.maimaidx_group_ranking import enroll_group_rating_user
            await enroll_group_rating_user(event, silent=True)
        
        from .command.mai_score import best50_handler
        async for result in best50_handler(event):
            yield result

    @filter.regex(r'^(b40|B40)\s*(.*)$')
    async def best40(self, event: AstrMessageEvent):
        """全谱面 Rating 前 40。"""
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import top_records_handler
        async for result in top_records_handler(event, "b40"):
            yield result

    @filter.regex(r'^(ap50|AP50)\s*(.*)$')
    async def ap50(self, event: AstrMessageEvent):
        """AP/AP+ 谱面 Rating 前 50。"""
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import top_records_handler
        async for result in top_records_handler(event, "ap50"):
            yield result

    @filter.regex(r'^(pc50|PC50)$')
    async def pc50(self, event: AstrMessageEvent):
        """按 sdgb 谱面游玩次数生成 PC50。"""
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import pc50_handler
        async for result in pc50_handler(event):
            yield result

    @filter.regex(r'^(pk50|PK50)\s+(.+)$')
    async def pk50(self, event: AstrMessageEvent):
        """比较两名玩家的 B50。"""
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import pk50_handler
        async for result in pk50_handler(event):
            yield result

    @filter.regex(r'^更新记录$')
    async def update_sdgb_records(self, event: AstrMessageEvent):
        """从精确绑定 userId 的最新 sdgb dump 安全导入记录。"""
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import update_sdgb_records_handler
        async for result in update_sdgb_records_handler(
            event,
            str(_config_get(self.config, "sdgb_dump_root", "") or "").strip(),
            _config_get(self.config, "sdgb_user_bindings", ""),
        ):
            yield result

    @filter.command_group("tech")
    def tech(self):
        """私有 SDGB Tech 后端指令。"""
        pass

    @tech.command("status", alias={"状态"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def tech_status(self, event: AstrMessageEvent):
        """查看 maibot 与私有后端状态。"""
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        try:
            status = await call_backend("status")
        except BridgeError as exc:
            yield event.plain_result(
                f"maibot Tech 前端："
                f"{'已启用' if self._tech_enabled() else '已关闭'}；"
                f"后端不可用：{exc.safe_message}"
            )
            return
        yield event.plain_result(
            f"maibot Tech 前端："
            f"{'已启用' if self._tech_enabled() else '已关闭'}；"
            f"私有后端：{'已启用' if status.get('enabled') else '已关闭'}；"
            f"{'允许写入' if status.get('write_enabled') else '只读'}；"
            f"{status.get('safe_message', '')}"
        )

    @tech.command("enable", alias={"开启"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def tech_enable(self, event: AstrMessageEvent):
        """由 AstrBot 超级管理员同时开启前端与私有后端。"""
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        try:
            await call_backend("set_enabled", enabled=True)
            self._set_tech_enabled(True)
        except BridgeError as exc:
            yield event.plain_result(f"SDGB Tech 无法启用：{exc.safe_message}")
            return
        yield event.plain_result("SDGB Tech 已启用；写入能力仍受后端独立开关控制")

    @tech.command("disable", alias={"关闭"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def tech_disable(self, event: AstrMessageEvent):
        """关闭 maibot Tech 前端并请求关闭私有后端。"""
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        self._set_tech_enabled(False)
        try:
            await call_backend("set_enabled", enabled=False)
        except BridgeError:
            yield event.plain_result("maibot Tech 前端已关闭；私有后端当前不可达")
            return
        yield event.plain_result("SDGB Tech 已关闭")

    @tech.command("capabilities", alias={"能力"})
    async def tech_capabilities(self, event: AstrMessageEvent):
        """查看私有后端动态提供的能力。"""
        from .command.mai_tech import format_capabilities
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        if not self._tech_user_allowed(event):
            return
        try:
            items = await call_backend("capabilities")
        except BridgeError as exc:
            yield event.plain_result(f"SDGB Tech 请求被拒绝：{exc.safe_message}")
            return
        yield event.plain_result(format_capabilities(items))

    @tech.command("preview", alias={"预览"})
    async def tech_preview(
        self,
        event: AstrMessageEvent,
        capability: str,
        parameters: str = "-",
    ):
        """预览私有能力；参数使用逗号分隔的 key=value。"""
        from .command.mai_tech import parse_parameters, validate_capability
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        if not self._tech_user_allowed(event):
            return
        try:
            preview = await call_backend(
                "preview",
                actor=self._tech_actor(event),
                capability=validate_capability(capability),
                parameters=parse_parameters(parameters),
            )
        except (BridgeError, ValueError) as exc:
            message = exc.safe_message if isinstance(exc, BridgeError) else str(exc)
            yield event.plain_result(f"SDGB Tech 请求被拒绝：{message}")
            return
        mode = "写入" if preview.get("write") else "只读"
        yield event.plain_result(
            f"预览 [{mode}]：{preview.get('summary')}\n"
            f"确认码：{preview.get('plan_id')}\n"
            f"{preview.get('expires_in')} 秒内发送："
            f"/tech confirm {preview.get('plan_id')}"
        )

    @tech.command("confirm", alias={"确认"})
    async def tech_confirm(self, event: AstrMessageEvent, plan_id: str):
        """确认同一管理员生成的短时计划。"""
        from .command.mai_tech import validate_identifier
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        if not self._tech_user_allowed(event):
            return
        try:
            job = await call_backend(
                "confirm",
                actor=self._tech_actor(event),
                plan_id=validate_identifier(plan_id, "确认码"),
            )
        except (BridgeError, ValueError) as exc:
            message = exc.safe_message if isinstance(exc, BridgeError) else str(exc)
            yield event.plain_result(f"SDGB Tech 请求被拒绝：{message}")
            return
        yield event.plain_result(
            f"任务已排队：{job.get('job_id')}\n内容：{job.get('summary')}"
        )

    @tech.command("job", alias={"任务"})
    async def tech_job(self, event: AstrMessageEvent, job_id: str):
        """查询当前管理员的私有后端任务。"""
        from .command.mai_tech import validate_identifier
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        if not self._tech_user_allowed(event):
            return
        try:
            job = await call_backend(
                "job_status",
                actor=self._tech_actor(event),
                job_id=validate_identifier(job_id, "任务号"),
            )
        except (BridgeError, ValueError) as exc:
            message = exc.safe_message if isinstance(exc, BridgeError) else str(exc)
            yield event.plain_result(f"SDGB Tech 请求被拒绝：{message}")
            return
        error = f"；错误码：{job.get('error_code')}" if job.get("error_code") else ""
        result = job.get("result")
        result_text = (
            f"\n结果：\n{result.get('text')}"
            if isinstance(result, dict) and result.get("text")
            else ""
        )
        yield event.plain_result(
            f"任务 {job.get('job_id')}：{job.get('state')}{error}\n"
            f"内容：{job.get('summary')}{result_text}"
        )

    @tech.command("cancel", alias={"取消"})
    async def tech_cancel(self, event: AstrMessageEvent, job_id: str):
        """取消尚未开始的私有后端任务。"""
        from .command.mai_tech import validate_identifier
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        if not self._tech_user_allowed(event):
            return
        try:
            job = await call_backend(
                "cancel",
                actor=self._tech_actor(event),
                job_id=validate_identifier(job_id, "任务号"),
            )
        except (BridgeError, ValueError) as exc:
            message = exc.safe_message if isinstance(exc, BridgeError) else str(exc)
            yield event.plain_result(f"SDGB Tech 请求被拒绝：{message}")
            return
        yield event.plain_result(
            f"任务 {job.get('job_id')}：{job.get('state')}"
        )

    @tech.command("group_enable", alias={"开启本群"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def tech_group_enable(self, event: AstrMessageEvent):
        """允许当前群普通用户使用全部已开放 Tech 能力。"""
        group_id = self._tech_group_id(event)
        if not group_id:
            return
        self._set_tech_group(group_id, True)
        yield event.plain_result("本群 Tech 功能已开启")

    @tech.command("group_disable", alias={"关闭本群"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def tech_group_disable(self, event: AstrMessageEvent):
        """关闭当前群普通用户的全部 Tech 能力。"""
        group_id = self._tech_group_id(event)
        if not group_id:
            return
        self._set_tech_group(group_id, False)
        yield event.plain_result("本群 Tech 功能已关闭")

    @tech.command("group_status", alias={"本群状态"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def tech_group_status(self, event: AstrMessageEvent):
        group_id = self._tech_group_id(event)
        if not group_id:
            return
        yield event.plain_result(
            "本群 Tech 功能："
            + ("已开启" if group_id in self._tech_enabled_groups() else "已关闭")
        )

    @tech.command("qr", alias={"二维码"})
    async def tech_qr(
        self,
        event: AstrMessageEvent,
        qr_code: str,
    ):
        """在已开放群中提交一次短时二维码。"""
        if not self._tech_user_allowed(event):
            return
        yield await self._tech_submit_qr(event, qr_code)

    @filter.regex(r"^SGWCMAID[^\s]{8,504}$")
    async def tech_qr_message(self, event: AstrMessageEvent):
        """直接识别群内短时二维码。"""
        if not self._tech_user_allowed(event):
            return
        yield await self._tech_submit_qr(
            event,
            event.message_str.strip(),
        )

    @tech.command("account", alias={"账号状态"})
    async def tech_account(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        from .libraries.sdgbtech_bridge import BridgeError, call_backend
        try:
            status = await call_backend(
                "credential_status",
                actor=self._tech_actor(event),
            )
        except BridgeError as exc:
            yield event.plain_result(f"账号状态：{exc.safe_message}")
            return
        identity = status.get("identity", {})
        yield event.plain_result(
            f"账号：{identity.get('nickname', '未知玩家')}；"
            f"Rating {identity.get('rating', 0)}；"
            f"{'已确认' if status.get('confirmed') else '等待确认'}；"
            f"剩余约 {status.get('expires_in', 0)} 秒"
        )

    @tech.command("revoke", alias={"撤销账号"})
    async def tech_revoke(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        from .libraries.sdgbtech_bridge import BridgeError, call_backend
        try:
            await call_backend(
                "revoke_credential",
                actor=self._tech_actor(event),
            )
        except BridgeError as exc:
            yield event.plain_result(f"撤销失败：{exc.safe_message}")
            return
        yield event.plain_result("短时二维码会话已撤销")

    @filter.regex(r"^解锁乐曲\s+\S.*$")
    async def tech_unlock_song(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        query = event.message_str.strip()[len("解锁乐曲"):].strip()
        song_id, error = self._tech_resolve_song(query)
        if error:
            yield event.plain_result(error)
            return
        yield await self._tech_start_operation(
            event,
            "song_unlock",
            {"song_id": str(song_id)},
        )

    @filter.regex(r"^发券\s+[235]$")
    async def tech_grant_ticket(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        multiplier = int(event.message_str.strip().split()[-1])
        if multiplier != 5:
            yield event.plain_result(
                "当前手册只确认新版本 5 倍票的 chargeId 和提交结构；"
                "2/3 倍票未确认，已拒绝执行"
            )
            return
        yield await self._tech_start_operation(
            event,
            "grant_5x_ticket",
            {},
        )

    @filter.regex(r"^开门\s+(青|白|紫|黑|黄|红)$")
    async def tech_open_gate(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        color = event.message_str.strip().split()[-1]
        gate_id = {
            "青": 1, "白": 2, "紫": 3,
            "黑": 4, "黄": 5, "红": 6,
        }[color]
        yield await self._tech_start_operation(
            event,
            "kaleidx_key",
            {"gate_id": str(gate_id), "open_gate": "true"},
        )

    @filter.regex(r"^获取收藏品\s+\S+\s+[1-9][0-9]*$")
    async def tech_grant_collection(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        _, collection_type, item_id = event.message_str.strip().split()
        normalized = {
            "plate": "plate",
            "姓名框": "plate",
            "名牌": "plate",
        }.get(collection_type.lower())
        if normalized is None:
            yield event.plain_result(
                "当前只有 plate/姓名框 的 itemKind=1 经过实测，"
                "其他收藏品类型已拒绝执行"
            )
            return
        yield await self._tech_start_operation(
            event,
            "collection_grant",
            {
                "collection_type": normalized,
                "item_id": item_id,
            },
        )

    @filter.regex(r"^增加舞里程\s+[0-9]{1,5}$")
    async def tech_add_mileage(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        delta = int(event.message_str.strip().split()[-1])
        if not 0 <= delta <= 99900:
            yield event.plain_result("舞里程增量必须为 0 到 99900")
            return
        if delta == 0:
            yield event.plain_result("舞里程增量为 0，未登录账号，也未修改任何数据")
            return
        yield await self._tech_start_operation(
            event,
            "mileage_add",
            {"delta": str(delta)},
        )

    @filter.regex(
        r"^修改乐曲数据\s+.+\s+(绿|黄|红|紫|白|宴)\s+"
        r"[0-9]{1,3}(?:\.[0-9]{1,4})?%?\s+"
        r"(none|fc|fc\+|ap|ap\+)\s+"
        r"(none|fs|fs\+|fdx|fdx\+|sync)\s+[0-5]$"
    )
    async def tech_merge_score(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        yield await self._tech_score_operation(
            event, "score_merge", "修改乐曲数据"
        )

    @filter.regex(
        r"^覆盖乐曲数据\s+.+\s+(绿|黄|红|紫|白|宴)\s+"
        r"[0-9]{1,3}(?:\.[0-9]{1,4})?%?\s+"
        r"(none|fc|fc\+|ap|ap\+)\s+"
        r"(none|fs|fs\+|fdx|fdx\+|sync)\s+[0-5]$"
    )
    async def tech_overwrite_score(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        yield await self._tech_score_operation(
            event, "score_overwrite", "覆盖乐曲数据"
        )

    @filter.regex(
        r"^下埋\s+.+\s+[0-9]{1,3}(?:\.[0-9]{1,4})?%?$"
    )
    async def tech_bury_score(self, event: AstrMessageEvent):
        if not self._tech_user_allowed(event):
            return
        body = event.message_str.strip()[len("下埋"):].strip()
        try:
            query, achievement = body.rsplit(None, 1)
        except ValueError:
            return
        song_id, error = self._tech_resolve_song(query.strip())
        if error:
            yield event.plain_result(error)
            return
        yield await self._tech_start_operation(
            event,
            "bury_score",
            {
                "song_id": str(song_id),
                "achievement": achievement,
            },
        )

    def _tech_enabled(self) -> bool:
        return _config_bool(self.config, "tech_enabled", False)

    def _tech_enabled_groups(self) -> set[str]:
        value = _config_get(self.config, "tech_enabled_groups", "")
        if isinstance(value, (list, tuple, set)):
            return {str(item).strip() for item in value if str(item).strip()}
        return {
            item.strip()
            for item in str(value or "").split(",")
            if item.strip()
        }

    @staticmethod
    def _tech_group_id(event: AstrMessageEvent) -> str:
        return str(getattr(event.message_obj, "group_id", "") or "").strip()

    @classmethod
    def _tech_actor(cls, event: AstrMessageEvent) -> str:
        """Bind QR sessions and jobs to both the QQ user and current group."""
        return (
            f"group:{cls._tech_group_id(event)}:"
            f"user:{str(event.get_sender_id()).strip()}"
        )

    def _tech_user_allowed(self, event: AstrMessageEvent) -> bool:
        group_id = self._tech_group_id(event)
        return bool(
            self._tech_enabled()
            and group_id
            and group_id in self._tech_enabled_groups()
        )

    def _set_tech_group(self, group_id: str, enabled: bool) -> None:
        groups = self._tech_enabled_groups()
        if enabled:
            groups.add(str(group_id))
        else:
            groups.discard(str(group_id))
        section = self.config.get("tech")
        if not isinstance(section, dict):
            section = {}
            self.config["tech"] = section
        section["tech_enabled_groups"] = ",".join(sorted(groups))
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()

    async def _tech_submit_qr(
        self,
        event: AstrMessageEvent,
        qr_code: str,
    ):
        import astrbot.api.message_components as Comp
        from .command.mai_base import convert_message_segment_to_chain
        from .command.mai_tech import draw_identity_b50
        from .libraries.sdgbtech_bridge import BridgeError, call_backend

        try:
            status = await call_backend(
                "submit_qr",
                actor=self._tech_actor(event),
                qr_code=str(qr_code),
            )
            identity = status.get("identity", {})
            picture = await draw_identity_b50(
                identity,
                str(event.get_sender_id()),
            )
            chain = convert_message_segment_to_chain(picture)
            chain.append(Comp.Plain(
                "\n二维码已生效；若不是目标账号，请直接发送新的 SGWCMAID 覆盖。\n"
                f"二维码会话剩余约 {status.get('expires_in', 0)} 秒"
            ))
            return event.chain_result(chain)
        except BridgeError as exc:
            return event.plain_result(f"二维码识别失败：{exc.safe_message}")
        except Exception:
            return event.plain_result("二维码已生效，但 B50 图片生成失败")

    async def _tech_start_operation(
        self,
        event: AstrMessageEvent,
        capability: str,
        parameters: dict[str, str],
    ):
        from .libraries.sdgbtech_bridge import BridgeError, call_backend
        try:
            preview = await call_backend(
                "preview",
                actor=self._tech_actor(event),
                capability=capability,
                parameters=parameters,
            )
            job = await call_backend(
                "confirm",
                actor=self._tech_actor(event),
                plan_id=str(preview["plan_id"]),
            )
        except BridgeError as exc:
            return event.plain_result(f"Tech 操作被拒绝：{exc.safe_message}")
        return event.plain_result(
            f"任务已排队：{job.get('job_id')}\n"
            f"内容：{job.get('summary')}\n"
            "本次二维码凭据已销毁，可用 /tech job <任务号> 查询"
        )

    async def _tech_score_operation(
        self,
        event: AstrMessageEvent,
        capability: str,
        prefix: str,
    ):
        body = event.message_str.strip()[len(prefix):].strip()
        try:
            query, difficulty, achievement, combo, sync, dx_stars = body.rsplit(
                None, 5
            )
        except ValueError:
            return event.plain_result(
                f"格式：{prefix} <歌曲ID/别名> <绿/黄/红/紫/白/宴> "
                "<完成度0.00-101.00> <none/fc/fc+/ap/ap+> "
                "<none/fs/fs+/fdx/fdx+/sync> <DX星级0-5>"
            )
        song_id, error = self._tech_resolve_song(query)
        if error:
            return event.plain_result(error)
        return await self._tech_start_operation(
            event,
            capability,
            {
                "song_id": str(song_id),
                "level": str(self._tech_difficulty_level(difficulty)),
                "achievement": achievement,
                "combo": combo,
                "sync": sync,
                "dx_stars": dx_stars,
            },
        )

    @staticmethod
    def _tech_difficulty_level(value: str) -> int:
        return {
            "绿": 0, "黄": 1, "红": 2, "紫": 3, "白": 4, "宴": 10,
        }[str(value)]

    @staticmethod
    def _tech_resolve_song(query: str) -> tuple[int | None, str | None]:
        if query.isdigit() and (music := mai.total_list.by_id(query)):
            return int(music.id), None
        if music := mai.total_list.by_title(query):
            return int(music.id), None
        aliases = mai.total_alias_list.by_alias(query)
        if not aliases:
            return None, "没有找到该乐曲或别名"
        if len(aliases) != 1:
            choices = "、".join(
                f"{item.SongID}:{item.Name}"
                for item in aliases[:8]
            )
            return None, f"别名不唯一，请改用歌曲 ID：{choices}"
        return int(aliases[0].SongID), None

    def _set_tech_enabled(self, enabled: bool) -> None:
        section = self.config.get("tech")
        if not isinstance(section, dict):
            section = {}
            self.config["tech"] = section
        section["tech_enabled"] = bool(enabled)
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()

    @filter.regex(r'^群(?i:rating)(?:\s+([1-9][0-9]*))?$')
    async def group_rating(self, event: AstrMessageEvent):
        """群 Rating 排行"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_group_ranking import group_rating_handler
        async for result in group_rating_handler(event):
            yield result

    @filter.regex(r'^加入群榜$')
    async def join_group_rating(self, event: AstrMessageEvent):
        """加入群 Rating 榜"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_group_ranking import join_group_rating_handler
        async for result in join_group_rating_handler(event):
            yield result

    @filter.regex(r'^退出群榜$')
    async def leave_group_rating(self, event: AstrMessageEvent):
        """退出群 Rating 榜"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_group_ranking import leave_group_rating_handler
        async for result in leave_group_rating_handler(event):
            yield result

    @filter.regex(r'^(群榜签名\s+.+|清除群榜签名)$')
    async def group_rating_signature(self, event: AstrMessageEvent):
        """设置群 Rating 榜签名"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_group_ranking import group_rating_signature_handler
        async for result in group_rating_signature_handler(event):
            yield result

    @filter.regex(r'^(?:(?i:grank)|群排行)\s+(.+)$')
    async def group_song_rank(self, event: AstrMessageEvent):
        """指定歌曲、指定难度的群内成绩排行"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_group_ranking import group_song_rank_handler
        async for result in group_song_rank_handler(event):
            yield result

    @filter.regex(r'^(minfo|Minfo|MINFO|info|Info|INFO)\s*(.*)$')
    async def minfo(self, event: AstrMessageEvent):
        """minfo/info 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import minfo_handler
        async for result in minfo_handler(event):
            yield result

    @filter.regex(r'^(ginfo|Ginfo|GINFO)\s*(.*)$')
    async def ginfo(self, event: AstrMessageEvent):
        """ginfo 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import ginfo_handler
        async for result in ginfo_handler(event):
            yield result

    @filter.regex(r'^(谱面统计|chart stats)\s+(.+)$')
    async def chart_statistics(self, event: AstrMessageEvent):
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import chart_statistics_handler
        async for result in chart_statistics_handler(event):
            yield result

    @filter.regex(r'^分数线\s*(.*)$')
    async def score(self, event: AstrMessageEvent):
        """分数线命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import score_handler
        async for result in score_handler(event):
            yield result

    @filter.regex(r'^([0-9]*\.?[0-9]+)的([0-9]*\.?[0-9]+)是多少分$')
    async def calculate_score(self, event: AstrMessageEvent):
        """计算分数命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import mai_score_calculate_handler
        async for result in mai_score_calculate_handler(event):
            yield result

    # 搜索命令
    @filter.regex(r'^(查歌|search)\s*(.*)$')
    async def search_music(self, event: AstrMessageEvent):
        """查歌/search 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_music_handler
        async for result in search_music_handler(event):
            yield result

    @filter.regex(r'^(定数查歌|search base)\s*(.*)$')
    async def search_base(self, event: AstrMessageEvent):
        """定数查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_base_handler
        async for result in search_base_handler(event):
            yield result

    @filter.regex(r'^(bpm查歌|search bpm)\s*(.*)$')
    async def search_bpm(self, event: AstrMessageEvent):
        """bpm查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_bpm_handler
        async for result in search_bpm_handler(event):
            yield result

    @filter.regex(r'^(曲师查歌|search artist)\s*(.*)$')
    async def search_artist(self, event: AstrMessageEvent):
        """曲师查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_artist_handler
        async for result in search_artist_handler(event):
            yield result

    @filter.regex(r'^(谱师查歌|search charter)\s*(.*)$')
    async def search_charter(self, event: AstrMessageEvent):
        """谱师查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_charter_handler
        async for result in search_charter_handler(event):
            yield result

    @filter.regex(r'^(版本查歌|search version)\s*(.*)$')
    async def search_version(self, event: AstrMessageEvent):
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import version_search_handler
        async for result in version_search_handler(event):
            yield result

    @filter.regex(r'^(.+?)(是什么歌|是啥歌)$')
    async def search_alias_song(self, event: AstrMessageEvent):
        """是什么歌/是啥歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_alias_song_handler
        async for result in search_alias_song_handler(event):
            yield result

    @filter.regex(r'^id\s?([0-9]+)$')
    async def query_chart(self, event: AstrMessageEvent):
        """id 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import query_chart_handler
        async for result in query_chart_handler(event):
            yield result

    # 猜歌命令
    @filter.regex(r'^猜歌$')
    async def guess_music(self, event: AstrMessageEvent):
        """猜歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_music_handler
        async for result in guess_music_handler(event):
            yield result

    @filter.regex(r'^猜曲绘$')
    async def guess_pic(self, event: AstrMessageEvent):
        """猜曲绘命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_pic_handler
        async for result in guess_pic_handler(event):
            yield result

    @filter.regex(r'^重置猜歌$')
    async def reset_guess(self, event: AstrMessageEvent):
        """重置猜歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import reset_guess_handler
        async for result in reset_guess_handler(event):
            yield result

    @filter.regex(r'^(开启|关闭)mai猜歌$')
    async def guess_on_off(self, event: AstrMessageEvent):
        """开启/关闭mai猜歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_on_off_handler
        async for result in guess_on_off_handler(event):
            yield result

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_music_solve(self, event: AstrMessageEvent):
        """猜歌答案监听（监听所有群消息）"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_music_solve_handler
        async for result in guess_music_solve_handler(event):
            yield result

    # 定数表/完成表命令
    @filter.command("更新定数表")
    async def update_table(self, event: AstrMessageEvent):
        """更新定数表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import update_table_handler
        async for result in update_table_handler(event, self.superusers):
            yield result

    @filter.command("更新完成表")
    async def update_plate(self, event: AstrMessageEvent):
        """更新完成表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import update_plate_handler
        async for result in update_plate_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(?!更新)(.+?)定数表$')
    async def rating_table(self, event: AstrMessageEvent):
        """定数表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import rating_table_handler
        async for result in rating_table_handler(event):
            yield result

    @filter.regex(r'^(?!更新)(.+?)完成表(?:\s+(.+))?$')
    async def table_pfm(self, event: AstrMessageEvent):
        """完成表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import table_pfm_handler
        async for result in table_pfm_handler(event):
            yield result

    @filter.regex(r'^我要在?([0-9]+\+?)?[上加\+]([0-9]+)?分\s?(.+)?$')
    async def rise_score(self, event: AstrMessageEvent):
        """我要在x+上x分命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import rise_score_handler
        async for result in rise_score_handler(event):
            yield result

    @filter.regex(r'^([真超檄橙暁晓桃櫻樱紫菫堇白雪輝辉舞霸熊華华爽煌星宙祭祝双宴镜彩])([極极将舞神者]舞?)进度\s?(.+)?$')
    async def plate_process(self, event: AstrMessageEvent):
        """牌子进度命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import plate_process_handler
        async for result in plate_process_handler(event):
            yield result

    @filter.regex(r'^([0-9]+\+?)([abcdsfxp\+]+)([\u4e00-\u9fa5]+)?进度\s?([0-9]+)?\s?(.+)?$')
    async def level_process(self, event: AstrMessageEvent):
        """等级进度命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import level_process_handler
        async for result in level_process_handler(event):
            yield result

    @filter.regex(r'^分类进度(?:\s+.*)?$')
    async def category_progress(self, event: AstrMessageEvent):
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import category_progress_handler
        async for result in category_progress_handler(event):
            yield result

    @filter.regex(r'^(段位表|段位课题)(?:\s*.*)?$')
    async def dan_table(self, event: AstrMessageEvent):
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import dan_table_handler
        async for result in dan_table_handler(event):
            yield result

    @filter.regex(r'^([0-9]+\.?[0-9]?\+?)分数列表\s?([0-9]+)?\s?(.+)?$')
    async def level_achievement_list(self, event: AstrMessageEvent):
        """分数列表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import level_achievement_list_handler
        async for result in level_achievement_list_handler(event):
            yield result

    # 别名相关命令
    @filter.command("更新别名库")
    async def update_alias(self, event: AstrMessageEvent):
        """更新别名库命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import update_alias_handler
        async for result in update_alias_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(全局开启别名推送|全局关闭别名推送)$')
    async def alias_switch_on_off(self, event: AstrMessageEvent):
        """全局开启/关闭别名推送命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_switch_on_off_handler
        async for result in alias_switch_on_off_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(添加本地别名|添加本地别称)\s+(.+)$')
    async def alias_local_apply(self, event: AstrMessageEvent):
        """添加本地别名命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_local_apply_handler
        async for result in alias_local_apply_handler(event):
            yield result

    @filter.regex(r'^(添加别名|增加别名|增添别名|添加别称)\s+(.+)$')
    async def alias_apply(self, event: AstrMessageEvent):
        """添加别名命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_apply_handler
        async for result in alias_apply_handler(event):
            yield result

    @filter.regex(r'^(同意别名|同意别称)\s+(.+)$')
    async def alias_agree(self, event: AstrMessageEvent):
        """同意别名命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_agree_handler
        async for result in alias_agree_handler(event):
            yield result

    @filter.regex(r'^(当前投票|当前别名投票|当前别称投票)\s*([0-9]+)?$')
    async def alias_status(self, event: AstrMessageEvent):
        """当前投票命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_status_handler
        async for result in alias_status_handler(event):
            yield result

    @filter.regex(r'^(开启|关闭)(别名推送|别称推送)$')
    async def alias_switch(self, event: AstrMessageEvent):
        """别名推送开关命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_switch_handler
        async for result in alias_switch_handler(event):
            yield result

    @filter.regex(r'^(id)?\s?(.+)\s?有什么别[名称]$')
    async def alias_song(self, event: AstrMessageEvent):
        """有什么别名命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_alias import alias_song_handler
        async for result in alias_song_handler(event):
            yield result

    # 机厅相关命令
    @filter.regex(r'^(帮助maimaiDX排卡|帮助maimaidx排卡)$')
    async def dx_arcade_help(self, event: AstrMessageEvent):
        """帮助maimaiDX排卡"""
        if not maiApi.config.arcade_enabled:
            yield event.plain_result('机厅排卡功能未启用，请由管理员在插件配置中开启')
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import dx_arcade_help_handler
        async for result in dx_arcade_help_handler(event):
            yield result

    @filter.regex(r'^(添加机厅|新增机厅)\s+(.+)$')
    async def add_arcade(self, event: AstrMessageEvent):
        """添加机厅"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import add_arcade_handler
        async for result in add_arcade_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(删除机厅|移除机厅)\s+(.+)$')
    async def delete_arcade(self, event: AstrMessageEvent):
        """删除机厅"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import delete_arcade_handler
        async for result in delete_arcade_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(添加机厅别名|删除机厅别名)\s+(.+)$')
    async def arcade_alias(self, event: AstrMessageEvent):
        """添加/删除机厅别名"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import arcade_alias_handler
        async for result in arcade_alias_handler(event):
            yield result

    @filter.regex(r'^(修改机厅|编辑机厅)\s+(.+)$')
    async def modify_arcade(self, event: AstrMessageEvent):
        """修改机厅"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import modify_arcade_handler
        async for result in modify_arcade_handler(event):
            yield result

    @filter.regex(r'^(订阅机厅|取消订阅机厅|取消订阅)\s(.+)$')
    async def subscribe_arcade(self, event: AstrMessageEvent):
        """订阅/取消订阅机厅"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import subscribe_arcade_handler
        async for result in subscribe_arcade_handler(event):
            yield result

    @filter.regex(r'^(查看订阅|查看订阅机厅)$')
    async def check_subscribe(self, event: AstrMessageEvent):
        """查看订阅"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import check_subscribe_handler
        async for result in check_subscribe_handler(event):
            yield result

    @filter.regex(r'^(查找机厅|查询机厅|机厅查找|机厅查询|搜素机厅|机厅搜素)\s+(.+)$')
    async def search_arcade(self, event: AstrMessageEvent):
        """查找机厅"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import search_arcade_handler
        async for result in search_arcade_handler(event):
            yield result

    @filter.regex(r'^(.+)?\s?(设置|设定|＝|=|增加|添加|加|＋|\+|减少|降低|减|－|-)\s?([0-9]+|＋|\+|－|-)(人|卡)?$')
    async def arcade_person(self, event: AstrMessageEvent):
        """操作排卡人数"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import arcade_person_handler
        async for result in arcade_person_handler(event):
            yield result

    @filter.regex(r'^(机厅几人|jtj)$')
    async def arcade_query_multiple(self, event: AstrMessageEvent):
        """机厅几人"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import arcade_query_multiple_handler
        async for result in arcade_query_multiple_handler(event):
            yield result

    @filter.regex(r'^(.+?)(有多少人|有几人|有几卡|多少人|多少卡|几人|jr|几卡)$')
    async def arcade_query_person(self, event: AstrMessageEvent):
        """有多少人/有几人/有几卡"""
        if not maiApi.config.arcade_enabled:
            return
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_arcade import arcade_query_person_handler
        async for result in arcade_query_person_handler(event):
            yield result

