import re

from astrbot.api.event import AstrMessageEvent

from ..libraries.maimaidx_identity import bind_event_qq, get_bound_qq, unbind_event_qq


async def bind_qq_handler(event: AstrMessageEvent):
    """绑定当前平台用户ID到QQ号。"""
    message_str = event.message_str.strip()
    match = re.match(r'^(绑定QQ|绑定qq)\s*([1-9][0-9]{4,11})$', message_str)
    if not match:
        yield event.plain_result('格式错误，请发送：绑定QQ 你的QQ号')
        return

    qq = match.group(2)
    try:
        await bind_event_qq(event, qq)
    except ValueError as e:
        yield event.plain_result(str(e))
        return
    yield event.plain_result(f'已绑定 QQ：{qq}\n后续 b50、完成表、牌子进度等查询将使用该 QQ。')


async def query_qq_bind_handler(event: AstrMessageEvent):
    """查看当前用户QQ绑定。"""
    qq = await get_bound_qq(event)
    if qq:
        yield event.plain_result(f'当前绑定 QQ：{qq}')
    else:
        yield event.plain_result('你还没有绑定 QQ。\n如果当前平台无法直接获取 QQ 号，请发送：绑定QQ 你的QQ号')


async def unbind_qq_handler(event: AstrMessageEvent):
    """解绑当前用户QQ。"""
    if await unbind_event_qq(event):
        yield event.plain_result('已解除 QQ 绑定')
    else:
        yield event.plain_result('你当前没有绑定 QQ')
