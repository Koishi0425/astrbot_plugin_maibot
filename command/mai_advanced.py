from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from .. import (
    MessageSegment, SONGS_PER_PAGE, all_version_names, category, combo_rank,
    diffs, is_reply_enabled, plate_to_dx_version, platecn, scoreRank, static,
)
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimai_best_50 import computeRa
from ..libraries.maimaidx_advanced import (
    chart_distribution, course_catalog, field, is_ap, load_sdgb_cache,
    record_level_index, record_song_id, sorted_records,
)
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_identity import (
    extract_at_user_id, is_numeric_qq, resolve_at_qq, resolve_sender_qq,
)
from ..libraries.maimaidx_music import mai


def _message(text: str) -> MessageSegment:
    return MessageSegment.image(image_to_base64(text_to_image(text.strip())))


def _chain(event: AstrMessageEvent, result: MessageSegment | str):
    from .mai_base import convert_message_segment_to_chain
    chain = convert_message_segment_to_chain(result)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    return chain


async def _identity(event: AstrMessageEvent, target: str = "") -> tuple[str | None, str | None, str | None]:
    """Return qqid, username, error."""
    if extract_at_user_id(event):
        identity = await resolve_at_qq(event)
        return (identity.qqid, None, identity.error)
    target = target.strip()
    if target:
        if is_numeric_qq(target):
            return (target, None, None)
        return (None, target, None)
    identity = await resolve_sender_qq(event)
    return (identity.qqid, None, identity.error)


async def _records(qqid: str | None, username: str | None) -> list[Any]:
    if maiApi.token:
        obj = await maiApi.query_user_get_dev(qqid=qqid, username=username)
        return list(obj.records or [])
    records = await maiApi.query_user_plate(qqid=qqid, username=username, version=all_version_names)
    for record in records:
        music = mai.total_list.by_id(record.song_id)
        if music and record.level_index < len(music.ds):
            record.ds = music.ds[record.level_index]
            record.ra, record.rate = computeRa(record.ds, record.achievements, israte=True)
    return records


def _record_line(index: int, record: Any, *, play_count: bool = False) -> str:
    song_id = record_song_id(record)
    level_index = record_level_index(record)
    music = mai.total_list.by_id(song_id)
    title = music.title if music else str(field(record, "title", f"ID {song_id}"))
    ds = music.ds[level_index] if music and level_index < len(music.ds) else field(record, "ds", "-")
    achievement = float(field(record, "achievements", field(record, "achievement", 0)) or 0)
    if achievement > 1000:  # sdgb stores 10000 == 1%
        achievement /= 10000
    suffix = f" PC {int(field(record, 'playCount', 0) or 0)}" if play_count else f" RA {int(field(record, 'ra', 0) or 0)}"
    return f"{index:02d}. [{song_id}] {diffs[level_index]} {ds} {achievement:.4f}%{suffix}  {title}"


async def version_search_handler(event: AstrMessageEvent):
    args = re.sub(r"^(版本查歌|search version)\s*", "", event.message_str.strip(), count=1, flags=re.I).strip()
    tokens = args.split()
    page = int(tokens[-1]) if tokens and tokens[-1].isdigit() else 1
    if tokens and tokens[-1].isdigit():
        tokens.pop()
    query = " ".join(tokens).strip()
    if not query:
        yield event.plain_result("用法：版本查歌 <版本名/牌子字/发布日期前缀> [页数]")
        return
    canonical = plate_to_dx_version.get(platecn.get(query, query), query)
    result = []
    for music in mai.total_list:
        info = music.basic_info
        if (
            canonical.lower() in info.version.lower()
            or query.lower() in info.version.lower()
            or str(info.release_date or "").startswith(query)
        ) and int(music.id) < 100000:
            result.append(music)
    if not result:
        yield event.plain_result(f"没有找到版本或日期为「{query}」的歌曲")
        return
    result.sort(key=lambda item: int(item.id))
    pages = max(1, (len(result) - 1) // SONGS_PER_PAGE + 1)
    page = max(1, min(page, pages))
    lines = [f"版本查歌：{query}（第 {page}/{pages} 页，共 {len(result)} 首）"]
    for music in result[(page - 1) * SONGS_PER_PAGE: page * SONGS_PER_PAGE]:
        lines.append(f"[{music.id}] {music.type} {music.basic_info.release_date or '-'}  {music.title}")
    yield event.chain_result(_chain(event, _message("\n".join(lines))))


async def top_records_handler(event: AstrMessageEvent, mode: str):
    target = re.sub(rf"^(?i:{mode})\s*", "", event.message_str.strip(), count=1).strip()
    qqid, username, error = await _identity(event, target)
    if error:
        yield event.plain_result(error)
        return
    try:
        records = await _records(qqid, username)
    except Exception as exc:
        yield event.plain_result(str(exc))
        return
    if mode.lower() == "ap50":
        selected = sorted_records(records, limit=50, predicate=is_ap)
        title = "AP50（按单谱 Rating 排序）"
    else:
        selected = sorted_records(records, limit=40)
        title = "B40（全谱面 Rating 前 40）"
    if not selected:
        yield event.plain_result("没有可用于生成该列表的成绩")
        return
    lines = [f"{title}  合计 RA：{sum(int(field(item, 'ra', 0) or 0) for item in selected)}"]
    lines.extend(_record_line(index, record) for index, record in enumerate(selected, 1))
    yield event.chain_result(_chain(event, _message("\n".join(lines))))


async def pk50_handler(event: AstrMessageEvent):
    target = re.sub(r"^(?i:pk50)\s*", "", event.message_str.strip(), count=1).strip()
    if not target:
        yield event.plain_result("用法：pk50 <对方QQ或水鱼用户名>")
        return
    own_identity = await resolve_sender_qq(event)
    if own_identity.error:
        yield event.plain_result(own_identity.error)
        return
    own_qq = own_identity.qqid
    other_qq, other_name, _ = await _identity(event, target)
    try:
        own = await maiApi.query_user_b50(qqid=own_qq)
        other = await maiApi.query_user_b50(qqid=other_qq, username=other_name)
    except Exception as exc:
        yield event.plain_result(str(exc))
        return
    own_records = list(own.charts.sd or []) + list(own.charts.dx or [])
    other_records = list(other.charts.sd or []) + list(other.charts.dx or [])
    own_total = sum(record.ra for record in own_records)
    other_total = sum(record.ra for record in other_records)
    own_map = {(record.song_id, record.level_index): record for record in own_records}
    other_map = {(record.song_id, record.level_index): record for record in other_records}
    comparisons = []
    for key in own_map.keys() | other_map.keys():
        left = own_map.get(key)
        right = other_map.get(key)
        delta = (left.ra if left else 0) - (right.ra if right else 0)
        record = left or right
        comparisons.append((abs(delta), delta, record))
    comparisons.sort(reverse=True, key=lambda item: item[0])
    lines = [
        f"PK50：{own.nickname or own.username or own_qq}  VS  {other.nickname or other.username or target}",
        f"总 Rating：{own_total} : {other_total}（差值 {own_total - other_total:+d}）",
        "差距最大的谱面（正数为左侧领先）：",
    ]
    for index, (_, delta, record) in enumerate(comparisons[:20], 1):
        music = mai.total_list.by_id(record.song_id)
        title = music.title if music else record.title
        lines.append(f"{index:02d}. {delta:+4d} [{record.song_id}] {diffs[record.level_index]} {title}")
    yield event.chain_result(_chain(event, _message("\n".join(lines))))


async def chart_statistics_handler(event: AstrMessageEvent):
    # Reuse ginfo's song/difficulty parsing, but emit the missing rates as text.
    args = re.sub(r"^(谱面统计|chart stats)\s*", "", event.message_str.strip(), count=1, flags=re.I).strip()
    tokens = args.rsplit(maxsplit=1)
    level_index = 3
    if len(tokens) == 2 and tokens[1].lower() in {"绿", "黄", "红", "紫", "白", "basic", "advanced", "expert", "master", "remaster", "re:master"}:
        aliases = {"绿": 0, "黄": 1, "红": 2, "紫": 3, "白": 4, "basic": 0, "advanced": 1, "expert": 2, "master": 3, "remaster": 4, "re:master": 4}
        args, difficulty = tokens
        level_index = aliases[difficulty.lower()]
    music = mai.total_list.by_id(args) or mai.total_list.by_title(args)
    if not music:
        matches = mai.total_list.filter(title_search=args)
        music = matches[0] if len(matches) == 1 else None
    if not music or level_index >= len(music.ds) or not music.stats or not music.stats[level_index]:
        yield event.plain_result("未找到对应谱面或该谱面暂无统计数据")
        return
    stats = music.stats[level_index]
    rates = chart_distribution(stats)
    counts = [float(item.cnt or 0) for item in music.stats if item]
    play_share = float(stats.cnt or 0) / sum(counts) * 100 if sum(counts) else 0
    info = (
        f"[{music.id}] {music.title}  {diffs[level_index]} {music.ds[level_index]}\n"
        f"统计样本：{round(stats.cnt or 0)}\n"
        f"FC率：{rates['fc_rate']:.2f}%\nAP率：{rates['ap_rate']:.2f}%\n"
        f"本曲各难度样本占比：{play_share:.2f}%\n"
        f"平均达成率：{float(stats.avg or 0):.4f}%\n拟合定数：{float(stats.fit_diff or 0):.2f}\n"
        "注：公开 chart_stats 不提供全服总玩家数，因此“游玩率”展示为本曲各难度间的样本占比。"
    )
    from ..libraries.maimaidx_player_score import music_global_data
    from .mai_base import convert_message_segment_to_chain
    chart = await music_global_data(music, level_index)
    chain = convert_message_segment_to_chain(chart)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    chain.append(Comp.Plain(info))
    yield event.chain_result(chain)


def _goal_completed(record: Any | None, goal: str) -> bool:
    if record is None:
        return False
    normalized = goal.lower().replace("+", "p")
    thresholds = {"s": 97, "sp": 98, "ss": 99, "ssp": 99.5, "sss": 100, "sssp": 100.5}
    if normalized in thresholds:
        return float(field(record, "achievements", 0) or 0) >= thresholds[normalized]
    fc_order = {"fc": 1, "fcp": 2, "ap": 3, "app": 4}
    if normalized in fc_order:
        return combo_rank.index(str(field(record, "fc", ""))) + 1 >= fc_order[normalized] if str(field(record, "fc", "")) in combo_rank else False
    return False


async def category_progress_handler(event: AstrMessageEvent):
    args = re.sub(r"^分类进度\s*", "", event.message_str.strip(), count=1).strip()
    if not args or args == "帮助":
        yield event.plain_result(
            "用法：分类进度 [版本=彩] [分类=舞萌] [等级=13+] [定数=13.7] "
            "[难度=紫] [类型=DX/SD] 目标=sss+/ap [状态=全部/已完成/未完成/未游玩] [页=1] [用户=用户名]"
        )
        return
    options: dict[str, str] = {}
    for token in args.split():
        if "=" in token:
            key, value = token.split("=", 1)
            options[key.lower()] = value
    goal = options.get("目标", "sss+")
    status = options.get("状态", "全部")
    try:
        page = max(1, int(options.get("页", "1")))
        ds_filter = float(options["定数"]) if options.get("定数") else None
    except ValueError:
        yield event.plain_result("页数必须是整数，定数必须是数字")
        return
    qqid, username, error = await _identity(event, options.get("用户", ""))
    if error:
        yield event.plain_result(error)
        return
    try:
        records = await _records(qqid, username)
    except Exception as exc:
        yield event.plain_result(str(exc))
        return
    record_map = {(record_song_id(record), record_level_index(record)): record for record in records}
    version = options.get("版本")
    if version:
        version = plate_to_dx_version.get(platecn.get(version, version), version)
    genre = options.get("分类")
    genre_code = category.get(genre, genre) if genre else None
    difficulty_alias = {"绿": 0, "黄": 1, "红": 2, "紫": 3, "白": 4}
    diff_filter = difficulty_alias.get(options.get("难度", ""))
    targets = []
    for music in mai.total_list:
        if int(music.id) >= 100000:
            continue
        if version and version.lower() not in music.basic_info.version.lower():
            continue
        if genre_code and category.get(music.basic_info.genre, music.basic_info.genre) != genre_code:
            continue
        if options.get("类型") and music.type.lower() != options["类型"].lower():
            continue
        for level_index, level in enumerate(music.level):
            if diff_filter is not None and level_index != diff_filter:
                continue
            if options.get("等级") and level != options["等级"]:
                continue
            if ds_filter is not None and music.ds[level_index] != ds_filter:
                continue
            record = record_map.get((int(music.id), level_index))
            completed = _goal_completed(record, goal)
            current = "未游玩" if record is None else "已完成" if completed else "未完成"
            if status != "全部" and current != status:
                continue
            targets.append((music, level_index, record, current))
    per_page = 60
    pages = max(1, (len(targets) - 1) // per_page + 1)
    if page > pages:
        yield event.plain_result(f"超出页数，共 {pages} 页")
        return
    completed_count = sum(1 for _, _, record, _ in targets if _goal_completed(record, goal))
    lines = [f"分类进度 目标={goal} 状态={status}  第 {page}/{pages} 页", f"当前筛选：{len(targets)} 张谱面，完成 {completed_count} 张"]
    for music, level_index, record, current in targets[(page - 1) * per_page: page * per_page]:
        achievement = float(field(record, "achievements", 0) or 0) if record else 0
        lines.append(f"{current} [{music.id}] {diffs[level_index]} {music.ds[level_index]} {achievement:.4f}% {music.title}")
    yield event.chain_result(_chain(event, _message("\n".join(lines))))


async def dan_table_handler(event: AstrMessageEvent):
    query = re.sub(r"^(段位表|段位课题)\s*", "", event.message_str.strip(), count=1).strip()
    catalog = course_catalog(static / "dan_courses_cn_prism_plus.json")
    courses = catalog["courses"]
    if not query:
        groups = "、".join(courses.keys())
        yield event.plain_result(f"{catalog['version']} 可查询段位：\n{groups}\n用法：段位表 十段")
        return
    if query.isdigit() and int(query) in range(1, 11):
        names = ["初段", "二段", "三段", "四段", "五段", "六段", "七段", "八段", "九段", "十段"]
        query = names[int(query) - 1]
    if query not in courses:
        yield event.plain_result(f"没有找到「{query}」；发送“段位表”查看可用段位")
        return
    lines = [f"{catalog['version']} · {query}"]
    for index, (title, chart_type, level, combo) in enumerate(courses[query], 1):
        lines.append(f"Track {index}: [{chart_type}] Lv.{level}  {title}  COMBO {combo}")
    lines.append(catalog.get("notes", ""))
    yield event.chain_result(_chain(event, _message("\n".join(lines))))


async def pc50_handler(event: AstrMessageEvent):
    identity = await resolve_sender_qq(event)
    cache_key = (
        str(identity.qqid)
        if not identity.error and identity.qqid
        else str(event.get_sender_id())
    )
    cache = load_sdgb_cache(static / "sdgb_music_cache.json", cache_key)
    if not cache:
        yield event.plain_result("没有 sdgb 成绩缓存，请先执行“更新记录”。")
        return
    selected = sorted_records(
        cache.get("records", []), limit=50,
        key=lambda record: (int(field(record, "playCount", 0) or 0), int(field(record, "achievement", 0) or 0)),
    )
    source = (
        "SDGB Tech"
        if cache.get("source") == "sdgbtech"
        else Path(cache.get("dumpDir", "")).name
    )
    lines = [f"PC50（按谱面游玩次数排序）  数据：{source}"]
    lines.extend(_record_line(index, record, play_count=True) for index, record in enumerate(selected, 1))
    yield event.chain_result(_chain(event, _message("\n".join(lines))))
