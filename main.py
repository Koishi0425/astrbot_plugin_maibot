from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
import traceback
import re
from pathlib import Path

from . import Root, log, loga, ratingdir, platedir, plate_to_dx_version, platecn, data_dir, _BOTNAME
from .libraries.maimai_best_50 import ScoreBaseImage
from .libraries.maimaidx_api_data import MaiConfig, maiApi
from .libraries.maimaidx_music import mai
from .libraries.maimaidx_storage import read_json, write_json, preserve_corrupt
from .command.mai_alias import sse_alias_server
import sys


def _config_get(config: dict, key: str, default=None):
    if key in config:
        return config.get(key, default)
    for section in ("basic", "prober", "alias", "arcade", "resource"):
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


@register("astrbot_plugin_maibot", "Glmg", "舞萌DX国服查分与图片识分插件", "1.8.4")
class MaimaiDXPlugin(Star):
    async def terminate(self):
        """Stop background notifications and schedules on plugin unload."""
        if self.alias_push_task is not None:
            self.alias_push_task.cancel()
            try:
                await self.alias_push_task
            except asyncio.CancelledError:
                pass
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()

        # 群组启用状态（存储禁用的群组ID）
        self.disabled_groups = set()  # 禁用插件的群组ID集合
        self.group_settings_available = False
        self.data_file = data_dir / "disabled_groups.json"
        self.score_recognizer = None
        self.score_ocr_lock = asyncio.Lock()
        self.alias_push_task = None

        # 从插件配置中读取 bot 名称并设置到 __init__.py
        bot_name = _config_get(self.config, "bot_name", "Bot")
        enable_reply = _config_bool(self.config, "enable_reply", True)
        maiApi.configure(
            MaiConfig(
                divingfish_client_id=str(
                    _config_get(self.config, "divingfish_client_id", "") or ""
                ).strip(),
                divingfish_client_secret=str(
                    _config_get(self.config, "divingfish_client_secret", "") or ""
                ).strip(),
                maimaidxproberproxy=_config_bool(
                    self.config, "maimaidxproberproxy", False
                ),
                maimaidxaliasproxy=_config_bool(
                    self.config, "maimaidxaliasproxy", False
                ),
                maimaidxaliaspush=_config_bool(self.config, "maimaidxaliaspush", False),
                maimaidxaliasvoting=_config_bool(
                    self.config, "maimaidxaliasvoting", False
                ),
                maimaidxaliaswhitelist=_config_bool(
                    self.config, "maimaidxaliaswhitelist", False
                ),
                saveinmem=_config_bool(self.config, "saveinmem", True),
                resource_local_path=str(
                    _config_get(self.config, "resource_local_path", "") or ""
                ).strip(),
                resource_source_url=str(
                    _config_get(self.config, "resource_source_url", "") or ""
                ).strip(),
                resource_check_on_startup=_config_bool(
                    self.config, "resource_check_on_startup", True
                ),
                arcade_enabled=_config_bool(self.config, "arcade_enabled", False),
            )
        )
        pkg_name = __name__.rsplit(".", 1)[
            0
        ]  # 获取包名，例如 'myplugins.astrbot_plugin_maimaidx'
        if pkg_name in sys.modules:
            module = sys.modules[pkg_name]
            # 更新内部变量和公共变量
            setattr(module, "_BOTNAME", bot_name)
            setattr(module, "BOTNAME", bot_name)
            setattr(module, "_ENABLE_REPLY", enable_reply)
            log.info(f"已设置 bot 名称: {bot_name}")
            log.info(f"引用回复（Reply）: {'开启' if enable_reply else '关闭'}")

        # 从 astrbot 配置文件中获取管理员ID列表
        # 根据文档：https://docs.astrbot.app/dev/star/plugin.html
        # 使用 context.get_config() 获取配置，字段名为 admins_id
        bot_config = context.get_config()
        admins = bot_config.get("admins_id", [])
        # 确保所有ID都是字符串格式
        self.superusers = [str(admin) for admin in admins] if admins else []

        if self.superusers:
            log.info(f"从 astrbot 配置中获取到管理员ID列表: {self.superusers}")
        else:
            log.warning('未找到任何管理员ID，某些需要管理员权限的命令可能无法使用')

    async def initialize(self):
        """Initialize independent features and always register recovery schedules."""
        for setup in (self._load_disabled_groups, self._setup_configuration):
            try:
                setup()
            except Exception:
                log.exception("Plugin setup stage failed: %s", setup.__name__)
        try:
            await self._load_maimai_data()
        except Exception:
            log.exception(
                "Music initialization failed; available cached data is retained"
            )
        if maiApi.config.arcade_enabled:
            try:
                await self._load_arcade_data()
            except Exception:
                log.exception("Arcade initialization failed")
        for setup in (
            self._load_images_to_memory,
            self._perform_initial_checks,
            self._setup_schedulers,
        ):
            try:
                setup()
            except Exception:
                log.exception("Plugin setup stage failed: %s", setup.__name__)
        log.info("Maimai plugin initialization completed")

    def _load_disabled_groups(self):
        """Load group restrictions and block group commands if recovery is needed."""
        self.group_settings_available = False
        try:
            if self.data_file.exists():
                data = read_json(self.data_file, dict)
                groups = data.get("disabled_groups")
                if not isinstance(groups, list) or any(
                    not isinstance(group, (str, int)) for group in groups
                ):
                    preserve_corrupt(self.data_file)
                    raise ValueError(
                        "Invalid disabled group settings; restore the preserved backup"
                    )
                self.disabled_groups = {str(group) for group in groups}
            else:
                self._save_disabled_groups()
            self.group_settings_available = True
        except (ValueError, OSError):
            log.exception("Group settings unavailable; group commands remain disabled")

    def _save_disabled_groups(self, groups: set | None = None):
        """Atomically persist a group restriction change before applying it.

        Args:
            groups: Proposed restrictions, or the current restrictions when None.
        """
        write_json(
            self.data_file,
            {
                "disabled_groups": sorted(
                    self.disabled_groups if groups is None else groups
                )
            },
        )

    def _is_group_enabled(self, group_id: str) -> bool:
        """Check group restrictions after a successful settings load.

        Args:
            group_id: Group identifier.

        Returns:
            Whether group commands are allowed.
        """
        return self.group_settings_available and group_id not in self.disabled_groups

    def _setup_configuration(self):
        """处理配置相关的初始化"""
        if maiApi.config.maimaidxproberproxy:
            log.info("正在使用代理服务器访问查分器")
        if maiApi.config.maimaidxaliasproxy:
            log.info("正在使用代理服务器访问别名服务器")
        maiApi.load_proxy()

        if maiApi.config.maimaidxaliaspush:
            log.info("别名推送为「开启」状态")
            self.alias_push_task = asyncio.create_task(sse_alias_server(self.context))
        else:
            log.info("别名推送为「关闭」状态")
        log.info(
            f"别名申请与投票为「{'开启' if maiApi.config.maimaidxaliasvoting else '关闭'}」状态"
        )
        loga.info(f'机厅排卡为「{"开启" if maiApi.config.arcade_enabled else "关闭"}」状态')

    async def _load_maimai_data(self):
        """Load independent public catalogs without discarding usable data."""
        for load in (mai.get_music, mai.get_plate_json, mai.get_music_alias):
            try:
                await load()
            except Exception:
                log.exception("Public catalog initialization failed: %s", load.__name__)
        mai.guess()

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
        """Refresh independent public catalogs daily, including offline recovery."""
        try:
            await self._load_maimai_data()
            log.info("Daily public catalog refresh completed")
        except Exception as e:
            log.error("Daily public catalog refresh failed: %s", e)

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
            yield event.plain_result("仅允许超级管理员执行此操作")
            return

        # 检查是否在群聊中
        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result("此命令仅在群聊中可用")
            return

        gid = str(group_id)
        message_str = event.message_str.strip()

        if not self.group_settings_available:
            yield event.plain_result(
                "群设置读取失败，请管理员恢复 disabled_groups.json 后重启插件。"
            )
            return
        enabled = message_str == "开启舞萌功能"
        if enabled == (gid not in self.disabled_groups):
            yield event.plain_result(
                f"本群舞萌功能已经是{'开启' if enabled else '关闭'}状态"
            )
            return
        groups = self.disabled_groups.copy()
        if enabled:
            groups.discard(gid)
        else:
            groups.add(gid)
        try:
            self._save_disabled_groups(groups)
        except (ValueError, OSError):
            log.exception("Failed to persist group restrictions")
            yield event.plain_result(
                "群设置保存失败，未修改本群状态，请管理员检查数据文件。"
            )
            return
        self.disabled_groups = groups
        yield event.plain_result(f"已{'开启' if enabled else '关闭'}本群的舞萌功能")

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

    @filter.regex(r'^mai授权\s*$')
    async def authorize_divingfish(self, event: AstrMessageEvent):
        """Create the requesting user's Diving-Fish consent link.

        Args:
            event: Message requesting authorization for the sender's QQ binding.

        Yields:
            A masked binding identity, user code and official consent URL.
        """
        from .libraries.maimaidx_identity import resolve_sender_qq
        from .libraries.maimaidx_oauth import OAuthError

        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        identity = await resolve_sender_qq(event)
        if not identity.ok:
            yield event.plain_result(identity.error)
            return
        try:
            result = await maiApi.oauth.authorize(str(identity.qqid))
        except OAuthError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(
            f"水鱼授权 · {result['binding_label']}\n"
            f"用户码：{result['user_code']}（{int(result['expires_in']) // 60} 分钟内有效）\n"
            f"{result['verification_uri_complete']}\n"
            "请核对页面上的绑定身份与当前用户码，确认授权后重新发送查分指令。请勿转发授权链接。"
        )

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

    @filter.regex(
        r"^mai识分(?:\s+(?:id|ach|diff|dx|round|tap|hold|slide|touch|break)=[\s\S]*)?\s*$"
    )
    async def mai_score_recognition(self, event: AstrMessageEvent):
        """Recognize a score image or validate editable manual score parameters.

        Args:
            event: Score image, image reply or manually completed score command.

        Yields:
            Result image or an editable command preserving missing values.
        """
        from astrbot.api.message_components import Image, Reply
        from astrbot.api.star import StarTools
        from time import perf_counter
        from .libraries.maimaidx_score_recognition import (
            RecognizedScore,
            ScoreRecognizer,
        )
        from .libraries.maimaidx_score_input import parse_manual_score
        from .libraries.maimaidx_score_image import render_score_image
        from .command.mai_score_input import score_completion_reply

        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        parameters = (
            getattr(event, "message_str", "mai识分")
            .strip()
            .removeprefix("mai识分")
            .strip()
        )
        chain = event.message_obj.message
        images = [part for part in chain if isinstance(part, Image)]
        if not images:
            images = [
                part
                for reply in chain
                if isinstance(reply, Reply)
                for part in (reply.chain or [])
                if isinstance(part, Image)
            ]
        if not parameters and len(images) > 1:
            yield event.plain_result("请每次发送一张结算图片。")
            return
        score = RecognizedScore("manual")
        musics = getattr(mai, "total_list", [])
        started = perf_counter()
        download_seconds = queue_seconds = recognition_seconds = 0.0
        try:
            if parameters:
                score = parse_manual_score(parameters, musics)
            elif images:
                path = Path(await images[0].convert_to_file_path())
                downloaded = perf_counter()
                download_seconds = downloaded - started
                async with self.score_ocr_lock:
                    recognition_started = perf_counter()
                    queue_seconds = recognition_started - downloaded
                    if self.score_recognizer is None:
                        self.score_recognizer = ScoreRecognizer(
                            StarTools.get_data_dir("astrbot_plugin_maibot") / "ocr"
                        )
                    score = await asyncio.to_thread(
                        self.score_recognizer.recognize,
                        path,
                        musics,
                    )
                    recognition_seconds = perf_counter() - recognition_started
            if score.warnings:
                logger.debug(
                    "Score recognition diagnostics: %s", "; ".join(score.warnings)
                )
            analysis = score.analysis
            if (
                analysis
                and analysis.status in ("unique", "ambiguous")
                and score.music_id
                and score.title
                and score.difficulty is not None
                and score.achievement is not None
            ):
                music = next(
                    (item for item in musics if str(item.id) == score.music_id),
                    None,
                )
                drawing_started = perf_counter()
                picture = await asyncio.to_thread(render_score_image, score, music)
                logger.info(
                    "Score image ready: download=%.2fs queue=%.2fs "
                    "recognition=%.2fs draw=%.2fs total=%.2fs",
                    download_seconds,
                    queue_seconds,
                    recognition_seconds,
                    perf_counter() - drawing_started,
                    perf_counter() - started,
                )
                yield event.chain_result([Image.fromBytes(picture)])
                return
            if score.aggregate:
                reason = "简略结算缺少各键型判定，请发送 Details 详细页，或手动补全。"
            elif analysis and analysis.reason:
                reason = analysis.reason
            elif score.warnings and not score.title:
                reason = score.warnings[0]
            elif not score.music_id or score.difficulty is None or not score.title:
                reason = "请附上结算图片，或补齐歌曲 id、达成率和判定详情；必要时补充 diff=紫。"
            else:
                reason = "未识别到完整信息，请手动补全。"
        except ImportError:
            logger.error("Score recognition dependencies are unavailable")
            reason = "OCR 识分依赖未安装，请管理员安装 requirements.txt；也可手动填写以下数据。"
        except ValueError as exc:
            reason = str(exc)
        except OSError:
            logger.exception("Score image or drawing resources are unavailable")
            reason = (
                "判定已识别，但绘图字体不可用，请管理员检查中文字体资源。"
                if score.rows
                else "图片或 OCR 模型无法读取，请重新发图或手动补全。"
            )
        except Exception:
            logger.exception("Score image recognition failed")
            reason = "识分失败，可重新发送清晰的完整结算图片，或手动补全以下指令。"
        logger.info(
            "Score input required: download=%.2fs queue=%.2fs "
            "recognition=%.2fs total=%.2fs",
            download_seconds,
            queue_seconds,
            recognition_seconds,
            perf_counter() - started,
        )
        reply = await score_completion_reply(event, score, reason)
        if reply is not None:
            yield reply

    @filter.regex(r"^(今日mai|今日舞萌|今日运势)$")
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
        if group_id and event.message_str.strip().lower() == "b50":
            from .libraries.maimaidx_group_ranking import enroll_group_rating_user

            try:
                await enroll_group_rating_user(event, silent=True)
            except (ValueError, OSError):
                log.exception(
                    "Unable to update group membership; continuing the score query"
                )

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

    @filter.regex(r'^(pk50|PK50)\s+(.+)$')
    async def pk50(self, event: AstrMessageEvent):
        """比较两名玩家的 B50。"""
        if event.message_obj.group_id and not self._is_group_enabled(str(event.message_obj.group_id)):
            return
        from .command.mai_advanced import pk50_handler
        async for result in pk50_handler(event):
            yield result

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

