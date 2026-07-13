import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import aiofiles
from PIL import Image, ImageDraw, ImageFont

from .. import SIYUAN, TBFONT, diffs, fcl, fsl, get_botname, group_rating_file, log, maimaidir, score_Rank_l, themepicdir
from .image import music_picture
from .maimaidx_api_data import maiApi
from .maimaidx_identity import get_identity_key, resolve_sender_qq
from .maimaidx_model import Music, PlayInfoDefault, PlayInfoDev, UserInfo


PAGE_SIZE = 10
CACHE_TTL_SECONDS = 300
_STORE_LOCK = asyncio.Lock()
_QUERY_CACHE: Dict[str, Tuple[float, UserInfo, Optional[bytes]]] = {}
_SONG_RECORD_CACHE: Dict[Tuple[str, str, int], Tuple[float, Optional[Union[PlayInfoDefault, PlayInfoDev]]]] = {}


@dataclass
class GroupRatingEntry:
    key: str
    platform_user_id: str
    qq: str
    display_name: str
    signature: str
    user: UserInfo
    avatar: Optional[bytes]
    rank: int = 0

    @property
    def rating(self) -> int:
        return int(self.user.rating or 0)

    @property
    def b35(self) -> int:
        charts = self.user.charts.sd if self.user.charts else None
        return sum(int(chart.ra or 0) for chart in (charts or []))

    @property
    def b15(self) -> int:
        charts = self.user.charts.dx if self.user.charts else None
        return sum(int(chart.ra or 0) for chart in (charts or []))


@dataclass
class GroupSongRankEntry:
    member: GroupRatingEntry
    record: Union[PlayInfoDefault, PlayInfoDev]
    rank: int = 0

    @property
    def key(self) -> str:
        return self.member.key


async def _load_store() -> Dict[str, Dict[str, dict]]:
    if not group_rating_file.exists():
        return {}
    try:
        async with aiofiles.open(group_rating_file, "r", encoding="utf-8") as file:
            content = await file.read()
        data = json.loads(content) if content.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning(f"读取群 Rating 榜数据失败: {exc}")
        return {}


async def _save_store(data: Dict[str, Dict[str, dict]]) -> None:
    group_rating_file.parent.mkdir(parents=True, exist_ok=True)
    temp_path = group_rating_file.with_suffix(".tmp")
    async with aiofiles.open(temp_path, "w", encoding="utf-8") as file:
        await file.write(json.dumps(data, ensure_ascii=False, indent=2))
    await asyncio.to_thread(temp_path.replace, group_rating_file)


async def _event_display_name(event) -> str:
    group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
    sender_id = str(event.get_sender_id())
    if group_id:
        try:
            info = await event.bot.get_group_member_info(group_id=group_id, user_id=sender_id)
            if info:
                return str(info.get("card") or info.get("nickname") or sender_id)
        except Exception:
            pass
    for name in ("get_sender_name", "get_sender_nickname"):
        getter = getattr(event, name, None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass
    return sender_id


async def enroll_group_rating_user(event, *, silent: bool = False) -> Tuple[bool, str]:
    group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
    if not group_id:
        return False, "群 Rating 榜仅在群聊中可用"

    identity = await resolve_sender_qq(event)
    if identity.error:
        return False, identity.error

    key = get_identity_key(event)
    display_name = await _event_display_name(event)
    async with _STORE_LOCK:
        store = await _load_store()
        members = store.setdefault(str(group_id), {})
        old = members.get(key, {})
        members[key] = {
            "platform_user_id": str(event.get_sender_id()),
            "qq": identity.qqid,
            "display_name": display_name,
            "signature": str(old.get("signature", ""))[:32],
            "joined_at": old.get("joined_at") or datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await _save_store(store)
    return True, "已加入本群 Rating 榜" if not silent else ""


async def leave_group_rating(event) -> Tuple[bool, str]:
    group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
    if not group_id:
        return False, "群 Rating 榜仅在群聊中可用"
    key = get_identity_key(event)
    async with _STORE_LOCK:
        store = await _load_store()
        members = store.get(str(group_id), {})
        existed = key in members
        if existed:
            del members[key]
            if not members:
                store.pop(str(group_id), None)
            await _save_store(store)
    return existed, "已退出本群 Rating 榜" if existed else "你还没有加入本群 Rating 榜"


async def set_group_rating_signature(event, signature: str) -> Tuple[bool, str]:
    group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
    if not group_id:
        return False, "群 Rating 榜仅在群聊中可用"
    signature = signature.strip()
    if len(signature) > 32:
        return False, "群榜签名最多 32 个字符"
    key = get_identity_key(event)
    async with _STORE_LOCK:
        store = await _load_store()
        member = store.get(str(group_id), {}).get(key)
        if not member:
            return False, "请先发送「加入群榜」或在本群查询一次 b50"
        member["signature"] = signature
        member["updated_at"] = datetime.now(timezone.utc).isoformat()
        await _save_store(store)
    return True, "已清除群榜签名" if not signature else f"群榜签名已设置为：{signature}"


async def _query_member(member_key: str, data: dict, semaphore: asyncio.Semaphore) -> Optional[GroupRatingEntry]:
    qq = str(data.get("qq", ""))
    if not qq:
        return None
    now = time.monotonic()
    cached = _QUERY_CACHE.get(qq)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        user, avatar = cached[1], cached[2]
    else:
        async with semaphore:
            try:
                user = await maiApi.query_user_b50(qqid=qq)
            except Exception as exc:
                log.warning(f"群 Rating 榜跳过用户 {qq}: {exc}")
                return None
            try:
                avatar = await maiApi.qqlogo(qqid=qq)
            except Exception as exc:
                log.warning(f"群 Rating 榜获取用户 {qq} 头像失败: {exc}")
                avatar = None
        _QUERY_CACHE[qq] = (now, user, avatar)
    return GroupRatingEntry(
        key=member_key,
        platform_user_id=str(data.get("platform_user_id", "")),
        qq=qq,
        display_name=str(data.get("display_name", "")),
        signature=str(data.get("signature", "")),
        user=user,
        avatar=avatar,
    )


async def get_group_rating_page(event, page: int = 1):
    group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
    if not group_id:
        return None, "群 Rating 榜仅在群聊中可用"
    store = await _load_store()
    members = store.get(str(group_id), {})
    if not members:
        return None, "本群还没有榜单成员，发送「加入群榜」或在本群查询一次 b50 即可加入"

    semaphore = asyncio.Semaphore(5)
    results = await asyncio.gather(
        *(_query_member(key, data, semaphore) for key, data in members.items())
    )
    entries = [entry for entry in results if entry is not None]
    if not entries:
        return None, "群榜成员暂时都无法从查分器获取成绩，请确认隐私设置或稍后重试"

    entries.sort(key=lambda item: (-item.rating, (item.user.username or "").lower(), item.qq))
    for index, entry in enumerate(entries, start=1):
        entry.rank = index

    total_pages = max(1, math.ceil(len(entries) / PAGE_SIZE))
    page = min(max(int(page or 1), 1), total_pages)
    start = (page - 1) * PAGE_SIZE
    page_entries = entries[start:start + PAGE_SIZE]
    requester_key = get_identity_key(event)
    requester = next((entry for entry in entries if entry.key == requester_key), None)
    requester_on_page = requester is not None and start < requester.rank <= start + len(page_entries)

    image = draw_group_rating(
        page_entries,
        page=page,
        total_pages=total_pages,
        total_members=len(entries),
        requester=requester,
        show_requester_card=bool(requester and not requester_on_page),
    )
    return image, None


async def _query_song_record(
    qq: str,
    music: Music,
    level_index: int,
    semaphore: asyncio.Semaphore,
) -> Optional[Union[PlayInfoDefault, PlayInfoDev]]:
    cache_key = (qq, music.id, level_index)
    now = time.monotonic()
    cached = _SONG_RECORD_CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    record = None
    async with semaphore:
        try:
            if maiApi.token:
                records = await maiApi.query_user_post_dev(qqid=qq, music_id=music.id)
            else:
                records = await maiApi.query_user_plate(
                    qqid=qq,
                    version=[music.basic_info.version],
                )
            record = next(
                (
                    item for item in records
                    if int(item.song_id) == int(music.id) and item.level_index == level_index
                ),
                None,
            )
        except Exception as exc:
            log.warning(f"单曲群排行查询用户 {qq} 的歌曲 {music.id} 失败: {exc}")
    _SONG_RECORD_CACHE[cache_key] = (now, record)
    return record


async def get_group_song_rank_page(event, music: Music, level_index: int, page: int = 1):
    group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
    if not group_id:
        return None, "单曲群排行仅在群聊中可用"
    if level_index >= len(music.ds):
        return None, f"《{music.title}》没有{diffs[level_index]}谱面"

    store = await _load_store()
    members = store.get(str(group_id), {})
    if not members:
        return None, "本群还没有榜单成员，发送「加入群榜」或在本群查询一次 b50 即可加入"

    semaphore = asyncio.Semaphore(5)

    async def query_one(key: str, data: dict):
        member = await _query_member(key, data, semaphore)
        if not member:
            return None
        record = await _query_song_record(member.qq, music, level_index, semaphore)
        return GroupSongRankEntry(member=member, record=record) if record else None

    results = await asyncio.gather(*(query_one(key, data) for key, data in members.items()))
    entries = [entry for entry in results if entry is not None]
    if not entries:
        return None, f"本群榜单成员还没有《{music.title}》{diffs[level_index]}谱面的成绩"

    entries.sort(
        key=lambda item: (
            -float(item.record.achievements or 0),
            -int(item.record.dxScore or 0),
            (item.member.user.username or "").lower(),
        )
    )
    for index, entry in enumerate(entries, start=1):
        entry.rank = index

    total_pages = max(1, math.ceil(len(entries) / PAGE_SIZE))
    page = min(max(int(page or 1), 1), total_pages)
    start = (page - 1) * PAGE_SIZE
    page_entries = entries[start:start + PAGE_SIZE]
    requester_key = get_identity_key(event)
    requester = next((entry for entry in entries if entry.key == requester_key), None)
    requester_on_page = requester is not None and start < requester.rank <= start + len(page_entries)
    image = draw_group_song_rank(
        page_entries,
        music=music,
        level_index=level_index,
        page=page,
        total_pages=total_pages,
        total_players=len(entries),
        requester=requester,
        show_requester_card=bool(requester and not requester_on_page),
    )
    return image, None


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _gradient(size: Tuple[int, int], top, bottom) -> Image.Image:
    strip = Image.new("RGB", (1, 2))
    strip.putpixel((0, 0), top)
    strip.putpixel((0, 1), bottom)
    return strip.resize(size)


def _prism_background(width: int, height: int) -> Image.Image:
    """Build a variable-height PRiSM PLUS background without stretching artwork."""
    background_path = themepicdir / "b50.png"
    if not background_path.exists():
        return _gradient((width, height), (184, 225, 255), (255, 205, 222)).convert("RGBA")

    source = Image.open(background_path).convert("RGBA")
    scaled_height = round(source.height * width / source.width)
    source = source.resize((width, scaled_height))

    top_height = min(190, height // 3)
    bottom_height = min(125, height // 4)
    middle_height = max(1, height - top_height - bottom_height)
    canvas = Image.new("RGBA", (width, height))
    canvas.paste(source.crop((0, 0, width, top_height)), (0, 0))

    # Only the quiet middle field is generated. Top sky and bottom city retain
    # their original aspect ratio for every leaderboard length.
    middle = _gradient((width, middle_height), (173, 193, 251), (255, 199, 215)).convert("RGBA")
    middle_draw = ImageDraw.Draw(middle)
    for y in range(12, middle_height, 18):
        offset = 9 if (y // 18) % 2 else 0
        for x in range(8 + offset, width, 18):
            middle_draw.ellipse((x, y, x + 2, y + 2), fill=(255, 255, 255, 92))
    canvas.alpha_composite(middle, (0, top_height))
    canvas.paste(
        source.crop((0, scaled_height - bottom_height, width, scaled_height)),
        (0, height - bottom_height),
    )
    return canvas


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    suffix = "…"
    while text and draw.textbbox((0, 0), text + suffix, font=font)[2] > max_width:
        text = text[:-1]
    return text + suffix


def _avatar_image(data: Optional[bytes], size: int) -> Image.Image:
    try:
        avatar = Image.open(BytesIO(data)).convert("RGB") if data else Image.new("RGB", (size, size), "#dceaf7")
    except Exception:
        avatar = Image.new("RGB", (size, size), "#dceaf7")
    avatar = avatar.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=22, fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(avatar, (0, 0), mask)
    return result


def _rank_color(rank: int) -> Tuple[str, str]:
    if rank == 1:
        return "#ffb52e", "#fff4ce"
    if rank == 2:
        return "#98a4b5", "#edf3fb"
    if rank == 3:
        return "#d8843d", "#ffe8d0"
    return "#7a86c8", "#f2edff"


def _draw_card(canvas: Image.Image, entry: GroupRatingEntry, top: int, *, compact: bool = False) -> None:
    draw = ImageDraw.Draw(canvas)
    left, right = 128, canvas.width - 62
    height = 112 if compact else 132
    badge, tint = _rank_color(entry.rank)
    # 与完成表/minfo 相同的半透明白色面板、紫蓝描边和轻投影。
    draw.rounded_rectangle((left + 6, top + 7, right + 6, top + height + 7), radius=22, fill=(91, 74, 116, 55))
    draw.rounded_rectangle((left, top, right, top + height), radius=22, fill=(255, 255, 255, 238), outline="#7c86ff", width=3)
    # 名次强调色完全收在卡片内部，避免贴住圆角后产生“超出框体”的错觉。
    draw.rounded_rectangle((left + 11, top + 16, left + 19, top + height - 16), radius=4, fill=badge)

    rank_box = (38, top + 29, 104, top + 91)
    draw.rounded_rectangle(rank_box, radius=18, fill=badge, outline="white", width=3)
    rank_font = _font(TBFONT, 31 if entry.rank < 100 else 24)
    rank_text = str(entry.rank)
    bbox = draw.textbbox((0, 0), rank_text, font=rank_font)
    draw.text(((rank_box[0] + rank_box[2] - (bbox[2] - bbox[0])) / 2, rank_box[1] + 12), rank_text, font=rank_font, fill="white")

    avatar_size = height - 24
    # Keep a clear gutter between the inset accent bar and all profile content.
    avatar_x, avatar_y = left + 38, top + 12
    draw.rounded_rectangle((avatar_x - 4, avatar_y - 4, avatar_x + avatar_size + 4, avatar_y + avatar_size + 4), radius=25, fill="#ffffff", outline="#71d5eb", width=4)
    canvas.alpha_composite(_avatar_image(entry.avatar, avatar_size), (avatar_x, avatar_y))
    text_x = avatar_x + avatar_size + 24
    name_font = _font(SIYUAN, 31 if not compact else 27)
    meta_font = _font(SIYUAN, 18)
    small_font = _font(SIYUAN, 17)
    rating_font = _font(TBFONT, 31 if not compact else 28)

    game_name = entry.user.nickname or entry.display_name or entry.user.username or entry.qq
    draw.text((text_x, top + 13), _fit_text(draw, game_name, name_font, 420), font=name_font, fill="#293454")
    rating_text = str(entry.rating)
    rating_bbox = draw.textbbox((0, 0), rating_text, font=rating_font)
    rating_width = max(190, rating_bbox[2] - rating_bbox[0] + 80)
    rating_left = right - rating_width - 18
    draw.rounded_rectangle((rating_left, top + 13, right - 18, top + 61), radius=15, fill="#727cff", outline="white", width=2)
    draw.text((rating_left + 15, top + 25), "RATING", font=_font(TBFONT, 17), fill="#dffcff")
    draw.text((right - 34 - (rating_bbox[2] - rating_bbox[0]), top + 17), rating_text, font=rating_font, fill="white")

    username = entry.user.username or "未设置水鱼用户名"
    plate = entry.user.plate or ""
    meta = f"水鱼 ID  {username}    B35  {entry.b35}    B15  {entry.b15}"
    draw.text((text_x, top + 59), _fit_text(draw, meta, meta_font, right - text_x - 32), font=meta_font, fill="#586382")
    if not compact:
        signature = entry.signature or (f"称号：{plate}" if plate else "可发送「群榜签名 文本」设置个性签名")
        draw.rounded_rectangle((text_x, top + 91, right - 20, top + 119), radius=10, fill=tint)
        draw.text((text_x + 12, top + 94), _fit_text(draw, signature, small_font, right - text_x - 48), font=small_font, fill="#606a89")


def draw_group_rating(
    entries: List[GroupRatingEntry],
    *,
    page: int,
    total_pages: int,
    total_members: int,
    requester: Optional[GroupRatingEntry],
    show_requester_card: bool,
) -> Image.Image:
    header_height = 190
    card_height = 145
    requester_height = 172 if show_requester_card else 0
    footer_height = 145
    height = header_height + len(entries) * card_height + requester_height + footer_height
    canvas = _prism_background(1200, height)
    draw = ImageDraw.Draw(canvas)

    # 顶部标题沿用现有 PRiSM PLUS 图片的白字紫蓝描边。
    title_font = _font(SIYUAN, 49)
    meta_font = _font(SIYUAN, 20)
    title = "群 RATING 排行"
    title_bbox = draw.textbbox((0, 0), title, font=title_font, stroke_width=7)
    title_x = (1200 - (title_bbox[2] - title_bbox[0])) / 2
    draw.text((title_x, 34), title, font=title_font, fill="white", stroke_width=7, stroke_fill="#727cff")
    page_text = f"第 {page}/{total_pages} 页   ·   有效成员 {total_members} 人"
    page_bbox = draw.textbbox((0, 0), page_text, font=meta_font)
    page_left = (1200 - (page_bbox[2] - page_bbox[0])) / 2 - 24
    draw.rounded_rectangle((page_left, 112, 1200 - page_left, 151), radius=18, fill=(255, 255, 255, 210), outline="#9ca5ff", width=2)
    draw.text(((1200 - (page_bbox[2] - page_bbox[0])) / 2, 119), page_text, font=meta_font, fill="#596185")

    logo_path = themepicdir / "logo.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((225, 105))
        canvas.alpha_composite(logo, (48, 24))

    chara_path = themepicdir / "chara_right.png"
    if chara_path.exists():
        chara = Image.open(chara_path).convert("RGBA")
        chara.thumbnail((180, 175))
        canvas.alpha_composite(chara, (995, 5))

    panel_bottom = header_height + len(entries) * card_height - 5
    draw.rounded_rectangle((20, header_height - 15, 1180, panel_bottom), radius=28, fill=(255, 255, 255, 65), outline=(255, 255, 255, 155), width=2)

    top = header_height
    for entry in entries:
        _draw_card(canvas, entry, top)
        top += card_height

    if show_requester_card and requester:
        label_top = top + 9
        draw.rounded_rectangle((128, label_top, 318, label_top + 33), radius=14, fill=(255, 255, 255, 220), outline="#8f98ff", width=2)
        draw.text((147, label_top + 5), "我的总榜位置", font=_font(SIYUAN, 18), fill="#596185")
        _draw_card(canvas, requester, top + 51, compact=True)
        top += requester_height

    footer_top = height - footer_height + 8
    draw.rounded_rectangle((62, footer_top, 1138, footer_top + 55), radius=20, fill=(113, 124, 255, 205), outline="white", width=2)
    hint = "查询 b50 自动加入 · 加入群榜 / 退出群榜 · 群榜签名 <文本> · 群rating <页数>"
    hint_font = _font(SIYUAN, 19)
    hint_bbox = draw.textbbox((0, 0), hint, font=hint_font)
    draw.text(((1200 - (hint_bbox[2] - hint_bbox[0])) / 2, footer_top + 14), hint, font=hint_font, fill="white")
    bot_name = str(get_botname() or "Bot").strip()
    bot_label = bot_name if bot_name.lower().endswith("bot") else f"{bot_name} Bot"
    status = f"Designed By Glmg & Generated By {bot_label}"
    draw.rounded_rectangle((175, footer_top + 71, 1025, footer_top + 116), radius=20, fill=(255, 255, 255, 225), outline="#a4aaff", width=2)
    status_font = _font(SIYUAN, 21)
    bbox = draw.textbbox((0, 0), status, font=status_font)
    draw.text(((1200 - (bbox[2] - bbox[0])) / 2, footer_top + 81), status, font=status_font, fill="#596185")
    return canvas.convert("RGB")


def _record_rate(record: Union[PlayInfoDefault, PlayInfoDev], ds: float) -> str:
    rate = str(record.rate or "")
    if rate.lower() in score_Rank_l:
        return score_Rank_l[rate.lower()]
    achievement = float(record.achievements or 0)
    thresholds = (
        (100.5, "SSSp"), (100.0, "SSS"), (99.5, "SSp"), (99.0, "SS"),
        (98.0, "Sp"), (97.0, "S"), (94.0, "AAA"), (90.0, "AA"),
        (80.0, "A"), (75.0, "BBB"), (70.0, "BB"), (60.0, "B"),
        (50.0, "C"), (0.0, "D"),
    )
    return next(label for minimum, label in thresholds if achievement >= minimum)


def _draw_song_rank_row(
    canvas: Image.Image,
    entry: GroupSongRankEntry,
    top: int,
    *,
    max_dx_score: int,
    ds: float,
    highlighted: bool = False,
    compact: bool = False,
) -> None:
    draw = ImageDraw.Draw(canvas)
    height = 88 if compact else 100
    left, right = 120, 1142
    badge, tint = _rank_color(entry.rank)
    outline = "#1ecde2" if highlighted else "#ffffff"
    width = 4 if highlighted else 2
    draw.rounded_rectangle((left + 5, top + 6, right + 5, top + height + 6), radius=22, fill=(85, 72, 111, 42))
    draw.rounded_rectangle((left, top, right, top + height), radius=22, fill=(255, 255, 255, 224), outline=outline, width=width)

    rank_box = (38, top + (height - 59) // 2, 99, top + (height - 59) // 2 + 59)
    draw.rounded_rectangle(rank_box, radius=17, fill=badge, outline="white", width=3)
    rank_font = _font(TBFONT, 29 if entry.rank < 100 else 23)
    rank_text = str(entry.rank)
    rank_bbox = draw.textbbox((0, 0), rank_text, font=rank_font)
    draw.text(((rank_box[0] + rank_box[2] - (rank_bbox[2] - rank_bbox[0])) / 2, rank_box[1] + 11), rank_text, font=rank_font, fill="white")

    member = entry.member
    record = entry.record
    name = member.user.nickname or member.display_name or member.user.username or member.qq
    name_font = _font(SIYUAN, 25 if not compact else 23)
    small_font = _font(SIYUAN, 16)
    draw.text((150, top + 17), _fit_text(draw, name, name_font, 280), font=name_font, fill="#26324f")
    identity = member.signature or f"水鱼 ID  {member.user.username or member.qq}"
    draw.text((151, top + 55), _fit_text(draw, identity, small_font, 285), font=small_font, fill="#7c8298")

    achievement = f"{float(record.achievements or 0):.4f}%"
    achievement_font = _font(TBFONT, 35 if not compact else 31)
    achievement_bbox = draw.textbbox((0, 0), achievement, font=achievement_font)
    draw.text((560 - (achievement_bbox[2] - achievement_bbox[0]) / 2, top + 27), achievement, font=achievement_font, fill="#075ca8")

    dx_text = f"{int(record.dxScore or 0)} / {max_dx_score}"
    draw.text((680, top + 40), dx_text, font=_font(TBFONT, 18), fill="#60657c")

    icon_x = 835
    if record.fc:
        icon_name = fcl.get(record.fc)
        icon_path = maimaidir / f"UI_MSS_MBase_Icon_{icon_name}.png" if icon_name else None
        if icon_path and icon_path.exists():
            icon = Image.open(icon_path).convert("RGBA").resize((46, 46))
            canvas.alpha_composite(icon, (icon_x, top + 27))
            icon_x += 50
    if record.fs:
        icon_name = fsl.get(record.fs)
        icon_path = maimaidir / f"UI_MSS_MBase_Icon_{icon_name}.png" if icon_name else None
        if icon_path and icon_path.exists():
            icon = Image.open(icon_path).convert("RGBA").resize((46, 46))
            canvas.alpha_composite(icon, (icon_x, top + 27))

    rate_path = themepicdir / f"UI_TTR_Rank_{_record_rate(record, ds)}.png"
    if rate_path.exists():
        rate = Image.open(rate_path).convert("RGBA")
        rate.thumbnail((130, 63))
        canvas.alpha_composite(rate, (right - rate.width - 25, top + (height - rate.height) // 2))


def draw_group_song_rank(
    entries: List[GroupSongRankEntry],
    *,
    music: Music,
    level_index: int,
    page: int,
    total_pages: int,
    total_players: int,
    requester: Optional[GroupSongRankEntry],
    show_requester_card: bool,
) -> Image.Image:
    header_height = 220
    row_height = 112
    requester_height = 126 if show_requester_card else 0
    footer_height = 155
    height = header_height + len(entries) * row_height + requester_height + footer_height
    canvas = _prism_background(1200, height)
    draw = ImageDraw.Draw(canvas)

    # Header card
    draw.rounded_rectangle((42, 30, 1158, 194), radius=30, fill=(255, 255, 255, 210), outline="#ffffff", width=3)
    cover_path = music_picture(music.id)
    if cover_path.exists():
        cover = Image.open(cover_path).convert("RGBA").resize((132, 132))
        mask = Image.new("L", cover.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 131, 131), radius=20, fill=255)
        canvas.paste(cover, (70, 46), mask)

    title_font = _font(SIYUAN, 32)
    draw.text((230, 55), _fit_text(draw, music.title, title_font, 540), font=title_font, fill="#26324f")
    type_color = "#5f88dc" if music.type == "DX" else "#8e9aaa"
    type_box = (230, 104, 310, 142)
    draw.rounded_rectangle(type_box, radius=16, fill=type_color)
    type_font = _font(TBFONT, 20)
    type_bbox = draw.textbbox((0, 0), music.type, font=type_font)
    type_x = (type_box[0] + type_box[2] - (type_bbox[2] - type_bbox[0])) / 2 - type_bbox[0]
    type_y = (type_box[1] + type_box[3] - (type_bbox[3] - type_bbox[1])) / 2 - type_bbox[1]
    draw.text((type_x, type_y), music.type, font=type_font, fill="white")

    diff_colors = ["#71d94b", "#f4bb21", "#ff7180", "#a958df", "#ca9af1"]
    diff_text = f"{diffs[level_index]}  {music.ds[level_index]}"
    diff_font = _font(SIYUAN, 20)
    diff_bbox = draw.textbbox((0, 0), diff_text, font=diff_font)
    diff_right = 340 + diff_bbox[2] - diff_bbox[0] + 36
    diff_box = (328, 104, diff_right, 142)
    draw.rounded_rectangle(diff_box, radius=16, fill=diff_colors[level_index])
    diff_x = (diff_box[0] + diff_box[2] - (diff_bbox[2] - diff_bbox[0])) / 2 - diff_bbox[0]
    diff_y = (diff_box[1] + diff_box[3] - (diff_bbox[3] - diff_bbox[1])) / 2 - diff_bbox[1]
    draw.text((diff_x, diff_y), diff_text, font=diff_font, fill="white")
    page_text = f"群内 Top {total_players}  ·  第 {page}/{total_pages} 页"
    draw.rounded_rectangle((328, 151, 650, 184), radius=14, fill="#8e9aaa")
    draw.text((350, 156), page_text, font=_font(SIYUAN, 17), fill="white")

    logo_path = themepicdir / "logo.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((225, 105))
        canvas.alpha_composite(logo, (875, 64))

    max_dx_score = sum(music.charts[level_index].notes) * 3
    top = header_height
    requester_key = requester.key if requester else None
    for entry in entries:
        _draw_song_rank_row(
            canvas,
            entry,
            top,
            max_dx_score=max_dx_score,
            ds=music.ds[level_index],
            highlighted=entry.key == requester_key,
        )
        top += row_height

    if show_requester_card and requester:
        draw.rounded_rectangle((120, top + 2, 310, top + 35), radius=14, fill=(255, 255, 255, 220), outline="#8f98ff", width=2)
        draw.text((139, top + 7), "我的总榜位置", font=_font(SIYUAN, 18), fill="#596185")
        _draw_song_rank_row(
            canvas,
            requester,
            top + 40,
            max_dx_score=max_dx_score,
            ds=music.ds[level_index],
            highlighted=True,
            compact=True,
        )
        top += requester_height

    footer_top = height - footer_height + 8
    if requester:
        hint = f"你的当前名次：第 {requester.rank} 名 / 共 {total_players} 人"
    else:
        hint = "你尚未加入群榜，或暂无该谱面成绩"
    draw.rounded_rectangle((135, footer_top, 1065, footer_top + 48), radius=20, fill=(255, 255, 255, 225), outline="#a4aaff", width=2)
    hint_font = _font(SIYUAN, 20)
    hint_bbox = draw.textbbox((0, 0), hint, font=hint_font)
    draw.text(((1200 - (hint_bbox[2] - hint_bbox[0])) / 2, footer_top + 11), hint, font=hint_font, fill="#596185")

    bot_name = str(get_botname() or "Bot").strip()
    bot_label = bot_name if bot_name.lower().endswith("bot") else f"{bot_name} Bot"
    credit = f"Designed By Glmg & Generated By {bot_label}"
    credit_font_size = 19
    credit_font = _font(SIYUAN, credit_font_size)
    max_credit_text_width = 976
    while credit_font_size > 15 and draw.textlength(credit, font=credit_font) > max_credit_text_width:
        credit_font_size -= 1
        credit_font = _font(SIYUAN, credit_font_size)
    credit = _fit_text(draw, credit, credit_font, max_credit_text_width)
    credit_bbox = draw.textbbox((0, 0), credit, font=credit_font)
    credit_width = credit_bbox[2] - credit_bbox[0]
    credit_height = credit_bbox[3] - credit_bbox[1]
    credit_bar_height = 40
    credit_bar_width = max(650, min(1040, credit_width + 64))
    credit_left = (1200 - credit_bar_width) / 2
    credit_top = footer_top + 69
    draw.rounded_rectangle(
        (credit_left, credit_top, credit_left + credit_bar_width, credit_top + credit_bar_height),
        radius=credit_bar_height // 2,
        fill=(255, 255, 255, 178),
        outline=(150, 158, 224, 185),
        width=2,
    )
    credit_x = (1200 - credit_width) / 2 - credit_bbox[0]
    credit_y = credit_top + (credit_bar_height - credit_height) / 2 - credit_bbox[1]
    draw.text((credit_x, credit_y), credit, font=credit_font, fill="#66709a")
    return canvas.convert("RGB")
