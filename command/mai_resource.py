from typing import Optional

from astrbot.api.event import AstrMessageEvent

from ..libraries.maimaidx_api_data import MaiConfig


async def update_maimai_resources_handler(
    event: AstrMessageEvent,
    superusers: list = None,
    config: Optional[MaiConfig] = None,
):
    """兼容旧资源更新指令，转到完整更新流程。"""
    from .mai_update import update_maimai_all_handler

    async for result in update_maimai_all_handler(event, superusers, config, require_resource_source=True):
        yield result
