import asyncio
import traceback

from astrbot.api.event import AstrMessageEvent

from .. import log
from ..libraries.maimaidx_resource import install_maimai_resources, refresh_resource_cache
from .mai_arcade import is_superuser


RESOURCE_UPDATE_LOCK = asyncio.Lock()


async def update_maimai_resources_handler(event: AstrMessageEvent, superusers: list = None):
    """下载并安装舞萌静态资源。"""
    if not is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return

    if RESOURCE_UPDATE_LOCK.locked():
        yield event.plain_result('已有舞萌资源更新任务正在执行，请稍后再试')
        return

    async with RESOURCE_UPDATE_LOCK:
        yield event.plain_result('开始下载舞萌资源，请稍候。资源包较大，期间请不要重复触发该指令。')

        try:
            result = await install_maimai_resources()
            try:
                refresh_resource_cache()
                cache_msg = '已刷新内存图片缓存。'
            except Exception as e:
                log.warning(f'刷新资源缓存失败: {e}')
                cache_msg = '资源已安装，但刷新内存图片缓存失败，请重载插件或重启 AstrBot。'

            msg = [
                '舞萌资源更新完成。',
                f'已复制文件数：{result.copied_files}',
                '已保留 static/config.json。',
                cache_msg,
            ]
            if result.skipped_files:
                msg.append(f'跳过文件：{", ".join(result.skipped_files)}')
            if result.missing_paths:
                msg.append('但以下预期资源仍缺失：')
                msg.extend(f'- {path}' for path in result.missing_paths)
                msg.append('请确认资源包是否完整，必要时重载插件后再次执行。')
            else:
                msg.append('预期资源检查通过。')
            yield event.plain_result('\n'.join(msg))
        except Exception as e:
            log.error(f'舞萌资源更新失败: {e}')
            log.error(traceback.format_exc())
            yield event.plain_result(f'舞萌资源更新失败：{e}')
