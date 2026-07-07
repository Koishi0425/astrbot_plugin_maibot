import asyncio
import traceback
from typing import Optional

from astrbot.api.event import AstrMessageEvent

from .. import log
from ..libraries.maimaidx_api_data import MaiConfig, maiApi
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_resource import ResourceInstallError, install_maimai_resources, refresh_resource_cache
from .mai_arcade import is_superuser


MAIMAI_UPDATE_LOCK = asyncio.Lock()


def _has_resource_source(config: MaiConfig) -> bool:
    return bool((config.resource_local_path or "").strip() or (config.resource_source_url or "").strip())


async def _update_runtime_data() -> str:
    await mai.get_music()
    await mai.get_plate_json()
    await mai.get_music_alias()
    mai.guess()

    music_count = len(mai.total_list) if hasattr(mai, "total_list") and mai.total_list else 0
    alias_count = len(mai.total_alias_list) if hasattr(mai, "total_alias_list") and mai.total_alias_list else 0
    return f"数据更新完成：歌曲 {music_count} 首，别名 {alias_count} 条。"


async def _update_static_resources(config: MaiConfig, require_source: bool) -> list[str]:
    if not _has_resource_source(config) and not require_source:
        return ["静态资源跳过：未配置本地资源路径或资源 URL。"]

    result = await install_maimai_resources(
        local_path=config.resource_local_path,
        url=config.resource_source_url,
    )

    try:
        refresh_resource_cache()
        cache_msg = "已刷新内存图片缓存。"
    except Exception as e:
        log.warning(f"刷新资源缓存失败: {e}")
        cache_msg = "资源已安装，但刷新内存图片缓存失败，请重载插件或重启 AstrBot。"

    msg = [
        "静态资源更新完成。",
        f"已复制文件数：{result.copied_files}",
        "安装目标：static/。",
        "配置已由 AstrBot WebUI 管理，未读写 static/config.json。",
        cache_msg,
    ]
    if result.source_paths:
        msg.append(f"资源来源：{', '.join(result.source_paths)}")
    if result.used_url:
        msg.append(f"资源 URL：{result.used_url}")
    if result.skipped_files:
        msg.append(f"跳过文件：{', '.join(result.skipped_files)}")
    if result.warnings:
        msg.append("资源警告：")
        msg.extend(f"- {warning}" for warning in result.warnings)
    if result.missing_paths:
        msg.append("但以下预期资源仍缺失：")
        msg.extend(f"- {path}" for path in result.missing_paths)
        msg.append("请确认资源包是否完整，必要时重载插件后再次执行。")
    else:
        msg.append("预期资源检查通过。")
    return msg


async def update_maimai_all_handler(
    event: AstrMessageEvent,
    superusers: list = None,
    config: Optional[MaiConfig] = None,
    require_resource_source: bool = False,
):
    """Update maimai runtime data and static resources in one admin command."""
    if not is_superuser(event, superusers):
        yield event.plain_result("仅允许超级管理员执行此操作")
        return

    if MAIMAI_UPDATE_LOCK.locked():
        yield event.plain_result("已有舞萌更新任务正在执行，请稍后再试")
        return

    async with MAIMAI_UPDATE_LOCK:
        yield event.plain_result("开始更新舞萌数据与静态资源，请稍候。")

        messages = []
        data_ok = False
        resource_ok = False
        resource_skipped = False

        try:
            messages.append(await _update_runtime_data())
            data_ok = True
        except Exception as e:
            log.error(f"maimai 数据更新失败: {e}")
            log.error(traceback.format_exc())
            messages.append(f"数据更新失败：{e}")

        resource_config = config or maiApi.config
        try:
            resource_messages = await _update_static_resources(resource_config, require_resource_source)
            messages.extend(resource_messages)
            resource_skipped = bool(resource_messages and resource_messages[0].startswith("静态资源跳过"))
            resource_ok = bool(resource_messages and not resource_skipped)
        except ResourceInstallError as e:
            messages.append(f"静态资源更新失败：{e}")
            messages.append("请联系管理员检查 WebUI 中的本地资源路径或资源 URL。")
        except Exception as e:
            log.error(f"舞萌静态资源更新失败: {e}")
            log.error(traceback.format_exc())
            messages.append(f"静态资源更新失败：{e}")

        if data_ok and resource_ok:
            title = "舞萌数据与静态资源更新完成。"
        elif data_ok and resource_skipped:
            title = "舞萌数据更新完成，静态资源已跳过。"
        elif data_ok:
            title = "舞萌数据更新完成，静态资源未完成。"
        elif resource_ok:
            title = "静态资源更新完成，舞萌数据未完成。"
        else:
            title = "舞萌更新未完成。"

        yield event.plain_result("\n".join([title, *messages]))
