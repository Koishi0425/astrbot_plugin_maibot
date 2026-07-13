import re
import tempfile

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from .. import is_reply_enabled
from ..libraries.maimaidx_group_ranking import (
    enroll_group_rating_user,
    get_group_rating_page,
    get_group_song_rank_page,
    leave_group_rating,
    set_group_rating_signature,
)
from ..libraries.maimaidx_music import mai


async def group_rating_handler(event: AstrMessageEvent):
    message = event.message_str.strip()
    match = re.match(r"^群rating(?:\s+([1-9][0-9]*))?$", message, re.IGNORECASE)
    page = int(match.group(1)) if match and match.group(1) else 1
    image, error = await get_group_rating_page(event, page)
    if error:
        yield event.plain_result(error)
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as file:
        image.save(file, format="PNG")
        path = file.name
    chain = [Comp.Image.fromFileSystem(path)]
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)


async def join_group_rating_handler(event: AstrMessageEvent):
    _, message = await enroll_group_rating_user(event)
    yield event.plain_result(message)


async def leave_group_rating_handler(event: AstrMessageEvent):
    _, message = await leave_group_rating(event)
    yield event.plain_result(message)


async def group_rating_signature_handler(event: AstrMessageEvent):
    message = event.message_str.strip()
    if message == "清除群榜签名":
        signature = ""
    else:
        signature = message[len("群榜签名"):].strip()
    _, result = await set_group_rating_signature(event, signature)
    yield event.plain_result(result)


def _resolve_song_and_difficulty(raw: str):
    raw = raw.strip()
    page = 1
    tokens = raw.split()
    if tokens:
        page_match = re.fullmatch(r"(?:第([1-9][0-9]*)页|[pP]([1-9][0-9]*))", tokens[-1])
        if page_match:
            page = int(page_match.group(1) or page_match.group(2))
            tokens.pop()

    difficulty_aliases = {
        "绿": 0, "basic": 0,
        "黄": 1, "advanced": 1,
        "红": 2, "expert": 2,
        "紫": 3, "master": 3,
        "白": 4, "remaster": 4, "re:master": 4,
    }
    level_index = 3
    if tokens and tokens[-1].lower() in difficulty_aliases:
        level_index = difficulty_aliases[tokens[-1].lower()]
        tokens.pop()
    query = " ".join(tokens).strip()
    if not query:
        return None, level_index, page, "请输入歌曲名、别名或 ID"

    music = None
    normalized_id = query[2:].strip() if query.lower().startswith("id") else query
    if normalized_id.isdigit():
        music = mai.total_list.by_id(normalized_id)
    if not music:
        music = mai.total_list.by_title(query)
    if not music and getattr(mai, "total_alias_list", None):
        aliases = mai.total_alias_list.by_alias(query.lower())
        if aliases:
            if len(aliases) > 1:
                choices = "、".join(f"{item.SongID}:{item.Name}" for item in aliases[:8])
                return None, level_index, page, f"该别名对应多首歌曲，请改用 ID：{choices}"
            music = mai.total_list.by_id(str(aliases[0].SongID))
    if not music:
        matches = mai.total_list.filter(title_search=query)
        if len(matches) == 1:
            music = matches[0]
        elif len(matches) > 1:
            choices = "、".join(f"{item.id}:{item.title}" for item in matches[:8])
            return None, level_index, page, f"找到多首相似歌曲，请改用 ID：{choices}"
    if not music:
        return None, level_index, page, f"未找到歌曲：{query}"
    if level_index >= len(music.ds):
        return None, level_index, page, f"《{music.title}》没有该难度谱面"
    return music, level_index, page, None


async def group_song_rank_handler(event: AstrMessageEvent):
    message = event.message_str.strip()
    args = re.sub(r"^(?:grank|群排行)\s+", "", message, flags=re.IGNORECASE).strip()
    music, level_index, page, error = _resolve_song_and_difficulty(args)
    if error:
        yield event.plain_result(error)
        return

    image, error = await get_group_song_rank_page(event, music, level_index, page)
    if error:
        yield event.plain_result(error)
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as file:
        image.save(file, format="PNG")
        path = file.name
    chain = [Comp.Image.fromFileSystem(path)]
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)
