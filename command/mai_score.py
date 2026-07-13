import re
import tempfile
from textwrap import dedent
from typing import Any, List
import math

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import log, is_reply_enabled
from ..command.mai_base import convert_message_segment_to_chain
from ..libraries.maimai_best_50 import generate
from ..libraries.maimaidx_identity import extract_at_user_id, is_numeric_qq, resolve_at_qq, resolve_sender_qq
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_play_data
from ..libraries.maimaidx_player_score import music_global_data
from ..libraries.maimaidx_scoreline import draw_scoreline


async def best50_handler(event: AstrMessageEvent):
    """b50/B50 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    username = re.sub(r"[MSG_ID:[^\]]*]", "", message_str.replace("b50", "").replace("B50", "")).strip()
    qqid = None

    if extract_at_user_id(event):
        username = ''
        identity = await resolve_at_qq(event)
    elif username:
        if is_numeric_qq(username):
            qqid = username
            username = ''
        identity = None
    else:
        identity = await resolve_sender_qq(event)

    if identity:
        if identity.error:
            yield event.plain_result(identity.error)
            return
        qqid = identity.qqid

    result = await generate(qqid, username)
    chain: List[Any] = convert_message_segment_to_chain(result)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)
    
    
async def minfo_handler(event: AstrMessageEvent):
    """minfo/info 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    qqid = None
    username = None
    message_str = event.message_str.strip().lower()
    # 移除命令前缀
    for prefix in ['minfo', 'info']:
        if message_str.startswith(prefix):
            args = message_str[len(prefix):].strip()
            break
    else:
        args = message_str

    if extract_at_user_id(event):
        identity = await resolve_at_qq(event)
    else:
        identity = await resolve_sender_qq(event)
    if identity.error:
        yield event.plain_result(identity.error)
        return
    qqid = identity.qqid

    if not args:
        yield event.plain_result('请输入曲目id或曲名')
        return

    if mai.total_list.by_id(args):
        songs = args
    elif by_t := mai.total_list.by_title(args):
        songs = by_t.id
    else:
        if not hasattr(mai, 'total_alias_list') or not mai.total_alias_list:
            yield event.plain_result('别名数据未加载，请稍后再试或联系管理员')
            return
        alias = mai.total_alias_list.by_alias(args)
        if not alias:
            yield event.plain_result('未找到曲目')
            return
        elif len(alias) != 1:
            msg = f'找到相同别名的曲目，请使用以下ID查询：\n'
            for songs in alias:
                msg += f'{songs.SongID}：{songs.Name}\n'
            yield event.plain_result(msg.strip())
            return
        else:
            songs = str(alias[0].SongID)
    pic = await draw_music_play_data(qqid=qqid, music_id=songs, username=username)
    chain = convert_message_segment_to_chain(pic)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)


async def ginfo_handler(event: AstrMessageEvent):
    """ginfo 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip().lower()
    # 移除命令前缀
    for prefix in ['ginfo']:
        if message_str.startswith(prefix):
            args = message_str[len(prefix):].strip()
            break
    else:
        args = message_str
    
    if not args:
        yield event.plain_result('请输入曲目id或曲名')
        return
    
    # 参数顺序统一为“歌曲在前、难度在后”，例如：ginfo 799 紫。
    # 不写难度时仍默认查询紫谱。
    parts = args.rsplit(maxsplit=1)
    if len(parts) == 2 and parts[1] in '绿黄红紫白':
        args, difficulty = parts
        level_index = '绿黄红紫白'.index(difficulty)
    else:
        level_index = 3
    
    if mai.total_list.by_id(args):
        id = args
    elif by_t := mai.total_list.by_title(args):
        id = by_t.id
    else:
        if not hasattr(mai, 'total_alias_list') or not mai.total_alias_list:
            yield event.plain_result('别名数据未加载，请稍后再试或联系管理员')
            return
        alias = mai.total_alias_list.by_alias(args)
        if not alias:
            yield event.plain_result('未找到曲目')
            return
        elif len(alias) != 1:
            msg = f'找到相同别名的曲目，请使用以下ID查询：\n'
            for songs in alias:
                msg += f'{songs.SongID}：{songs.Name}\n'
            yield event.plain_result(msg.strip())
            return
        else:
            id = str(alias[0].SongID)

    music = mai.total_list.by_id(id)
    if not music.stats:
        yield event.plain_result('该乐曲还没有统计信息')
        return
    if len(music.ds) == 4 and level_index == 4:
        yield event.plain_result('该乐曲没有这个等级')
        return
    if not music.stats[level_index]:
        yield event.plain_result('该等级没有统计信息')
        return
    stats = music.stats[level_index]
    info = dedent(f'''\
        游玩次数：{round(stats.cnt)}
        拟合难度：{stats.fit_diff:.2f}
        平均达成率：{stats.avg:.2f}%
        平均 DX 分数：{stats.avg_dx:.1f}
        谱面成绩标准差：{stats.std_dev:.2f}''')
    pic = await music_global_data(music, level_index)
    chain = convert_message_segment_to_chain(pic)
    # 添加引用回复
    if not chain or not isinstance(chain[0], Comp.Reply):
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    chain.append(Comp.Plain(info))
    yield event.chain_result(chain)
    
    
async def score_handler(event: AstrMessageEvent):
    """生成指定歌曲、指定难度的完整分数线解析图。"""
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return

    args = re.sub(r'^分数线\s*', '', event.message_str.strip(), count=1).strip()
    if not args or args == '帮助':
        yield event.plain_result(
            '用法：分数线 <歌曲名/别名/ID> [难度]\n'
            '难度可用：绿/黄/红/紫/白 或 Basic/Advanced/Expert/Master/Re:Master\n'
            '不填写难度时默认查询紫谱 Master。\n'
            '例如：分数线 FFT、分数线 820 白'
        )
        return

    difficulty_aliases = {
        '绿': 0, 'basic': 0,
        '黄': 1, 'advanced': 1,
        '红': 2, 'expert': 2,
        '紫': 3, 'master': 3,
        '白': 4, 'remaster': 4, 're:master': 4,
    }
    tokens = args.split()
    level_index = 3
    if tokens and tokens[-1].lower() in difficulty_aliases:
        level_index = difficulty_aliases[tokens.pop().lower()]
    query = ' '.join(tokens).strip()
    if not query:
        yield event.plain_result('请输入歌曲名、别名或 ID')
        return

    music = None
    normalized_id = query[2:].strip() if query.lower().startswith('id') else query
    if normalized_id.isdigit():
        music = mai.total_list.by_id(normalized_id)
    if not music:
        music = mai.total_list.by_title(query)
    if not music and getattr(mai, 'total_alias_list', None):
        aliases = mai.total_alias_list.by_alias(query.lower())
        if aliases:
            if len(aliases) > 1:
                choices = '、'.join(f'{item.SongID}:{item.Name}' for item in aliases[:8])
                yield event.plain_result(f'该别名对应多首歌曲，请改用 ID：{choices}')
                return
            music = mai.total_list.by_id(str(aliases[0].SongID))
    if not music:
        matches = mai.total_list.filter(title_search=query)
        if len(matches) == 1:
            music = matches[0]
        elif len(matches) > 1:
            choices = '、'.join(f'{item.id}:{item.title}' for item in matches[:8])
            yield event.plain_result(f'找到多首相似歌曲，请改用 ID：{choices}')
            return
    if not music:
        yield event.plain_result(f'未找到歌曲：{query}')
        return
    if level_index >= len(music.ds):
        yield event.plain_result(f'《{music.title}》没有该难度谱面')
        return

    try:
        image = draw_scoreline(music, level_index)
    except (AttributeError, IndexError, ValueError) as exc:
        log.warning(f'生成分数线失败：{exc}')
        yield event.plain_result(f'《{music.title}》的该谱面缺少有效音符数据')
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as file:
        image.save(file, format='PNG')
        path = file.name
    chain = [Comp.Image.fromFileSystem(path)]
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)

async def mai_score_calculate_handler(event: AstrMessageEvent):
    """计算指定定数和达成率的分数"""
    message_str = event.message_str.strip()
    # 1. 匹配字符串提取 a (定数) 和 b (达成率)
    pattern = r'^([0-9]*\.?[0-9]+)的([0-9]*\.?[0-9]+)是多少分$'
    match = re.match(pattern, message_str)
    
    if not match:
        return

    a = float(match.group(1))
    b_raw = float(match.group(2))
    
    # 2. 达成率限制：100.5以上的都记为100.5
    b = min(b_raw, 100.5)

    # 3. 定义评级系数查找逻辑 (左闭右开)
    def get_coefficient(val):
        # 处理最高点的特殊情况
        if val >= 100.5 and val <= 101: return 0.224
        
        # 定义区间配置 (下限, 上限, 系数)
        thresholds = [
            (10, 20, 0.016), (20, 30, 0.032), (30, 40, 0.048),
            (40, 50, 0.064), (50, 60, 0.08), (60, 70, 0.096),
            (70, 75, 0.112), (75, 80, 0.128), (80, 90, 0.136),
            (90, 94, 0.152), (94, 97, 0.168), (97, 98, 0.2),
            (98, 99, 0.203), (99, 99.5, 0.208), (99.5, 100, 0.211),
            (100, 100.5, 0.216)
        ]
        
        for low, high, coef in thresholds:
            if low <= val < high:
                return coef
        return 0 # 不在区间内返回0

    coef = get_coefficient(b)

    # 4. 计算分数：定数 * 达成率 * 系数，然后向下取整
    # 注意：计算时达成率通常作为数值直接相乘（如 13.2 * 100.5 * 0.224）
    score = math.floor(a * b * coef)
    if score:
        yield event.plain_result(f"{a}的{b_raw}是{score}分")
        return
    else:
        yield event.plain_result("输入错误，可能是格式不正确或达成率或定数输入不正确。正确格式：{定数}的{达成率}是多少分")
        return
