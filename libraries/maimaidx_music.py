import asyncio
import random
from collections import Counter, defaultdict
from copy import deepcopy
from typing import List, Tuple

import numpy as np
from aiohttp import ClientError
from PIL import Image

from .. import *
from .image import image_to_base64, music_picture
from .maimaidx_api_data import maiApi
from .maimaidx_error import *
from .maimaidx_error import ServerError, UnknownError
from .maimaidx_model import *
from .maimaidx_model import Music, Stats, Alias, GuessSwitch, AliasesPush
from .. import data_dir, music_file, chart_file, alias_file, local_alias_file, guess_file, group_alias_file, log
from .tool import openfile, writefile
from .maimaidx_storage import read_json, preserve_corrupt

_FETCH_ERRORS = (ClientError, asyncio.TimeoutError, ServerError, UnknownError, ValueError, TypeError, KeyError)
_GROUP_SETTINGS_LOCK = asyncio.Lock()


def cross(
    checker: Union[List[str], List[float]], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]], 
    diff: List[int]
) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    if isinstance(elem, List):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if __e in elem:
                diff_ret.append(_j)
                ret = True
    elif isinstance(elem, Tuple):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem[0] <= __e <= elem[1]:
                diff_ret.append(_j)
                ret = True
    else:
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem == __e:
                diff_ret.append(_j)
                ret = True
    return ret, diff_ret


def in_or_equal(
    checker: Union[str, int], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]]
) -> bool:
    if elem is Ellipsis:
        return True
    if isinstance(elem, List):
        return checker in elem
    elif isinstance(elem, Tuple):
        return elem[0] <= checker <= elem[1]
    else:
        return checker == elem


class MusicList(List[Music]):
    
    def by_id(self, music_id: Union[str, int]) -> Optional[Music]:
        for music in self:
            if music.id == str(music_id):
                return music
        return None

    def by_title(self, music_title: str) -> Optional[Music]:
        for music in self:
            if music.title == music_title:
                return music
        return None
    
    def by_plan(
        self, 
        level: str
    ) -> Dict[str, Union[PlanInfo, RaMusic, Dict[int, Union[PlanInfo, RaMusic]]]]:
        lv = defaultdict(dict)
        
        def create_ra_music(music: Music, index: int) -> RaMusic:
            return RaMusic(
                id=music.id, 
                ds=music.ds[index], 
                lv=str(index), 
                lvp=music.level[index], 
                type=music.type
            )
        
        for music in self:
            if level not in music.level:
                continue
            if int(music.id) >= 100000:
                continue
            if music.level.count(level) > 1: # 同曲有相同等级
                lv[music.id] = { 
                    index: create_ra_music(music, index)
                    for index, _lv in enumerate(music.level) 
                    if _lv == level 
                }
            else:
                index = music.level.index(level)
                lv[music.id] = create_ra_music(music, index)
        return dict(lv)
    
    def by_level_list(self) -> Dict[str, Dict[str, List[RaMusic]]]:
        
        def level_range(lv: str) -> range:
            if lv == '15':
                return range(1)
            if lv.endswith('+'):
                return range(9, 5, -1)
            return range(9, -1, -1) if int(lv) <= 5 else range(5, -1, -1)
        
        _level = {
            lv: {f"{lv.rstrip('+')}.{i}": [] for i in level_range(lv)} for lv in levelList
        }
        for music in self:
            if int(music.id) >= 100000:
                continue
            for index, ds in enumerate(music.ds):
                if ds < 7:
                    continue
                ra = RaMusic(
                    id=music.id,
                    ds=ds,
                    lv=str(index),
                    lvp=music.level[index],
                    type=music.type
                )
                _level[music.level[index]][str(ds)].append(ra)
        return _level
    
    def by_id_list(self, music_id_list: List[int]) -> Optional[List[Music]]:
        musicList = []
        for music in self:
            if int(music.id) in music_id_list:
                musicList.append(music)
        return musicList
    
    def random(self) -> Music:
        return random.choice(self)

    def filter(
        self,
        *,
        level: Optional[Union[str, List[str]]] = ...,
        ds: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        title_search: Optional[str] = ...,
        artist_search: Optional[str] = ...,
        charter_search: Optional[str] = ...,
        genre: Optional[Union[str, List[str]]] = ...,
        bpm: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        type: Optional[Union[str, List[str]]] = ...,
        diff: List[int] = ...,
        version: Union[str, List[str]] = ...
    ) -> 'MusicList':
        new_list = MusicList()
        for music in self:
            diff2 = diff
            music = deepcopy(music)
            ret, diff2 = cross(music.level, level, diff2)
            if not ret:
                continue
            ret, diff2 = cross(music.ds, ds, diff2)
            if not ret:
                continue
            ret, diff2 = search_charts(music.charts, charter_search, diff2)
            if not ret:
                continue
            if not in_or_equal(music.basic_info.genre, genre):
                continue
            if not in_or_equal(music.type, type):
                continue
            if not in_or_equal(music.basic_info.bpm, bpm):
                continue
            if not in_or_equal(music.basic_info.version, version):
                continue
            if title_search is not Ellipsis and title_search.lower() not in music.title.lower():
                continue
            if artist_search is not Ellipsis and artist_search.lower() not in music.basic_info.artist.lower():
                continue
            music.diff = diff2
            new_list.append(music)
        return new_list


def search_charts(checker: List[Chart], elem: str, diff: List[int]) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    for _j in (range(len(checker)) if diff is Ellipsis else diff):
        if elem.lower() in checker[_j].charter.lower():
            diff_ret.append(_j)
            ret = True
    return ret, diff_ret


class AliasList(List[Alias]):

    def by_id(self, music_id: Union[str, int]) -> Optional[List[Alias]]:
        alias_music = []
        for music in self:
            if music.SongID == int(music_id):
                alias_music.append(music)
        return alias_music
    
    def by_alias(self, music_alias: str) -> Optional[List[Alias]]:
        alias_list = []
        for music in self:
            if music_alias in music.Alias:
                alias_list.append(music)
        return alias_list


dataerror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/music_data" 
    将内容保存到 "{music_file}" 并重启bot
''').strip()
charterror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/chart_stats"
    将内容保存到 "{chart_file}" 并重启bot
''').strip()
aliaserror = dedent(f'''
    本地暂存别名文件为空，请自行使用浏览器访问 "https://www.yuzuchan.moe/api/v2/aliases/maimaidx/aliases"
    获取别名数据并保存在 "{alias_file}" 文件中并重启bot
''').strip()


async def get_music_list() -> MusicList:
    """Load validated public catalogs, falling back to cached data on service failures.

    Returns:
        Available songs, with optional chart statistics.

    Raises:
        OSError: No readable music catalog is available.
        ValueError: Neither the response nor the cached music catalog is valid.
    """
    try:
        music_data = await maiApi.music_data()
        if not isinstance(music_data, list) or not music_data:
            raise ValueError("Empty or invalid music catalog")
        total_list = MusicList(Music.model_validate(item) for item in music_data)
    except _FETCH_ERRORS:
        log.warning("Music service unavailable; loading cached catalog")
        music_data = await openfile(music_file)
        if not isinstance(music_data, list) or not music_data:
            raise ValueError("No valid cached music catalog")
        total_list = MusicList(Music.model_validate(item) for item in music_data)
    else:
        try:
            await writefile(music_file, music_data, protect_corrupt=False)
        except OSError:
            log.exception("Unable to save the fresh music cache")

    try:
        chart_stats = await maiApi.chart_stats()
        if not isinstance(chart_stats, dict) or not isinstance(
            chart_stats.get("charts"), dict
        ):
            raise ValueError("Invalid chart statistics")
        for values in chart_stats["charts"].values():
            if not isinstance(values, list):
                raise ValueError("Invalid chart statistics row")
            for value in values:
                if value:
                    Stats.model_validate(value)
    except _FETCH_ERRORS:
        log.warning("Chart statistics service unavailable; loading cached statistics")
        try:
            chart_stats = await openfile(chart_file)
        except (OSError, ValueError):
            chart_stats = {"charts": {}}
            log.warning(
                "Chart statistics unavailable; basic song queries remain usable"
            )
    else:
        try:
            await writefile(chart_file, chart_stats, protect_corrupt=False)
        except OSError:
            log.exception("Unable to save the fresh chart statistics cache")
    if not isinstance(chart_stats, dict) or not isinstance(
        chart_stats.get("charts"), dict
    ):
        if chart_file.exists():
            preserve_corrupt(chart_file)
        chart_stats = {"charts": {}}
    for music in total_list:
        values = chart_stats.get("charts", {}).get(music.id, [])
        try:
            music.stats = [
                Stats.model_validate(value) if value else None for value in values
            ]
        except (ValueError, TypeError):
            music.stats = []
            log.warning(
                "Ignoring invalid cached chart statistics for song %s", music.id
            )
    return total_list


async def get_music_alias_list() -> AliasList:
    """Load remote or cached aliases and retain locally configured song aliases.

    Returns:
        Available aliases, including song titles when no remote data is available.
    """
    local_alias_data = {}
    try:
        if local_alias_file.exists():
            local_alias_data = await asyncio.to_thread(
                read_json, local_alias_file, dict
            )
    except (OSError, ValueError):
        log.exception("Local alias settings are unreadable and have been preserved")
    try:
        alias_data = await maiApi.get_alias()
        if not isinstance(alias_data, list) or not alias_data:
            raise ValueError("Empty or invalid alias catalog")
        aliases = [Alias.model_validate(item) for item in alias_data]
    except _FETCH_ERRORS:
        log.warning("Alias service unavailable; loading cached aliases")
        try:
            aliases = [
                Alias.model_validate(item) for item in await openfile(alias_file)
            ]
        except (OSError, ValueError, TypeError):
            aliases = []
            log.warning(
                "Remote alias cache unavailable; using song titles and local aliases"
            )
    else:
        try:
            await writefile(alias_file, alias_data, protect_corrupt=False)
        except OSError:
            log.exception("Unable to save the fresh alias cache")
    indexed = {item.SongID: item for item in aliases}
    total_alias_list = AliasList()
    for music in mai.total_list:
        item = indexed.get(int(music.id)) or Alias(
            SongID=int(music.id), Name=music.title, Alias=[music.title.lower()]
        )
        local = local_alias_data.get(music.id, [])
        if isinstance(local, list):
            item.Alias.extend(name for name in local if isinstance(name, str))
        item.Alias = list(dict.fromkeys(item.Alias))
        total_alias_list.append(item)
    return total_alias_list


async def update_local_alias(id: str, alias_name: str) -> bool:
    try:
        if local_alias_file.exists():
            local_alias_data: Dict[str, List[str]] = await openfile(local_alias_file)
        else:
            local_alias_data: Dict[str, List[str]] = {}
        if id not in local_alias_data:
            local_alias_data[id] = []
        
        local_alias_data[id].append(alias_name.lower())
        await writefile(local_alias_file, local_alias_data)
        mai.total_alias_list.by_id(id)[0].Alias.append(alias_name.lower())
        return True
    except Exception as e:
        log.error(f'添加本地别名失败: {e}')
        return False


class MaiMusic:
    
    total_list: MusicList
    """曲目数据"""
    total_alias_list: AliasList
    """别名数据"""
    total_plate_id_list: Dict[str, List[int]]
    """牌子ID列表数据"""
    total_level_data: Dict[str, Dict[str, List[RaMusic]]]
    """等级列表数据"""
    hot_music_ids: List = []
    """游玩次数超过1w次的曲目数据"""
    guess_data: List[Music]
    """猜歌数据"""

    def __init__(self) -> None:
        """Keep usable empty catalogs before the first successful data refresh."""
        self.total_list = MusicList()
        self.total_alias_list = AliasList()
        self.total_plate_id_list = {}
        self.total_level_data = {}
        self.hot_music_ids = []
        self.guess_data = []

    async def get_music(self) -> None:
        """获取所有曲目数据"""
        self.total_list = await get_music_list()
        self.total_level_data = self.total_list.by_level_list()

    async def get_music_alias(self) -> None:
        """获取所有曲目别名"""
        self.total_alias_list = await get_music_alias_list()

    async def get_plate_json(self) -> None:
        """Load public plate IDs, preserving cached IDs when the service is offline."""
        cache = data_dir / "music_plate.json"
        try:
            payload = await maiApi.get_plate_json()
            if not isinstance(payload, dict) or any(
                not isinstance(ids, list)
                or any(not isinstance(value, int) for value in ids)
                for ids in payload.values()
            ):
                raise ValueError("Invalid plate catalog")
        except _FETCH_ERRORS:
            log.warning("Plate service unavailable; retaining cached plate IDs")
            if cache.exists():
                self.total_plate_id_list = await asyncio.to_thread(
                    read_json, cache, dict
                )
            return
        self.total_plate_id_list = payload
        try:
            await writefile(cache, payload, protect_corrupt=False)
        except OSError:
            log.exception("Unable to save the fresh plate cache")

    def guess(self):
        """初始化猜歌数据"""
        self.hot_music_ids = []
        for music in self.total_list:
            if music.stats:
                count = 0
                for stats in music.stats:
                    if stats:
                        count += stats.cnt if stats.cnt else 0
                if count > 10000:
                    self.hot_music_ids.append(music.id)
        self.guess_data = list(
            filter(lambda x: x.id in self.hot_music_ids, self.total_list)
        )


mai = MaiMusic()


class Guess:
    
    Group: Dict[str, Union[GuessDefaultData, GuessPicData]] = {}  # 使用字符串类型作为键，group_id 在 AstrBotMessage 中是字符串
    switch: GuessSwitch

    def __init__(self) -> None:
        """Load group controls without allowing damaged settings to stop imports."""
        self.switch = GuessSwitch()
        self.storage_available = True
        try:
            if guess_file.exists():
                payload = read_json(guess_file, dict)
                for field in ("enable", "disable"):
                    if isinstance(payload.get(field), list):
                        payload[field] = [
                            str(value) for value in payload[field] if value is not None
                        ]
                self.switch = GuessSwitch.model_validate(payload)
        except (OSError, ValueError):
            self.storage_available = False
            try:
                if guess_file.exists():
                    preserve_corrupt(guess_file)
            except OSError:
                log.warning("Unable to create a guess settings backup")
            log.exception("Guess settings unavailable; group games remain disabled")

    def start(self, group_id: str):
        """开始猜歌"""
        self.Group[group_id] = self.guessData()

    def startpic(self, group_id: str):
        """开始猜曲绘"""
        self.Group[group_id] = self.guesspicdata()
        
    def calculate_frequency_weights(self, image: Image.Image) -> np.ndarray:
        """
        计算图像的频率权重，用于在图像中选择裁剪区域
        
        Params:
            `image`: PIL.Image.Image, 输入图像
        Returns:
            `np.ndarray` 频率权重矩阵
        """
        gray_image = np.array(image.convert('L'))
        freq = np.fft.fft2(gray_image)
        freq_shift = np.fft.fftshift(freq)
        magnitude = np.abs(freq_shift)
        normalized_magnitude = magnitude / magnitude.max()
        weights = normalized_magnitude ** 2
        return weights
    
    def select_crop_region(
        self, 
        weights: np.ndarray, 
        crop_width: int, 
        crop_height: int, 
        top_p: int
    ) -> Tuple[int, int]:
        h, w = weights.shape
        valid_regions = weights[:h - crop_height + 1, :w - crop_width + 1]
        flattened_weights = valid_regions.flatten()
        threshold = np.percentile(flattened_weights, top_p)
        valid_indices = np.where(flattened_weights >= threshold)[0]
        probabilities = flattened_weights[valid_indices]
        probabilities /= probabilities.sum()
        chosen_index = np.random.choice(valid_indices, p=probabilities)
        top_left_y = chosen_index // valid_regions.shape[1]
        top_left_x = chosen_index % valid_regions.shape[1]
        return top_left_x, top_left_y

    def pic(self, music: Music) -> Image.Image:
        """裁切曲绘"""
        im = Image.open(music_picture(music.id))
        w, h = im.size
        weights = self.calculate_frequency_weights(im)
        scale = random.uniform(0.15, 0.4)  # 裁剪尺寸范围 可在此修改
        w2, h2 = int(w * scale), int(h * scale)
        top_p = min(1.3 - np.power(scale, 0.4), 0.95) * 100
        x, y = self.select_crop_region(weights, w2, h2, top_p)
        im = im.crop((x, y, x + w2, y + h2))
        return im

    def guesspicdata(self) -> GuessPicData:
        """猜曲绘数据"""
        if not mai.guess_data:
            raise ValueError("猜歌数据未初始化，请先调用 mai.guess() 初始化数据")
        music = random.choice(mai.guess_data)
        pic = self.pic(music)
        alias_list = mai.total_alias_list.by_id(music.id)
        if not alias_list or len(alias_list) == 0:
            # 如果没有别名数据，使用歌曲ID和标题作为答案
            answer = [str(music.id), music.title]
        else:
            answer = alias_list[0].Alias.copy() if hasattr(alias_list[0], 'Alias') else [str(music.id), music.title]
            answer.append(str(music.id))
        return GuessPicData(music=music, img=image_to_base64(pic), answer=answer, end=False)

    def guessData(self) -> GuessDefaultData:
        """猜歌数据"""
        if not mai.guess_data:
            raise ValueError("猜歌数据未初始化，请先调用 mai.guess() 初始化数据")
        music = random.choice(mai.guess_data)
        guess_options = random.sample([
            f'的 Expert 难度是 {music.level[2]}',
            f'的 Master 难度是 {music.level[3]}',
            f'的分类是 {music.basic_info.genre}',
            f'的版本是 {music.basic_info.version}',
            f'的艺术家是 {music.basic_info.artist}',
            f'{"不" if music.type == "SD" else ""}是 DX 谱面',
            f'{"没" if len(music.ds) == 4 else ""}有白谱',
            f'的 BPM 是 {music.basic_info.bpm}'
        ], 6)
        alias_list = mai.total_alias_list.by_id(music.id)
        if not alias_list or len(alias_list) == 0:
            # 如果没有别名数据，使用歌曲ID和标题作为答案
            answer = [str(music.id), music.title]
        else:
            answer = alias_list[0].Alias.copy() if hasattr(alias_list[0], 'Alias') else [str(music.id), music.title]
            answer.append(str(music.id))
        pic = self.pic(music)
        return GuessDefaultData(
            music=music, 
            img=image_to_base64(pic), 
            answer=answer, 
            end=False, 
            options=guess_options
        )

    def end(self, group_id: str):
        """结束猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        if group_id in self.Group:
            del self.Group[group_id]

    async def on(self, group_id: str) -> str:
        """Persist the requested group enablement before changing active settings.

        Args:
            group_id: Group identifier.

        Returns:
            Confirmation or recovery instructions.
        """
        if not self.storage_available:
            return "猜歌群设置损坏，请管理员恢复 group_guess_switch.json 后重启插件。"
        async with _GROUP_SETTINGS_LOCK:
            proposed = self.switch.model_copy(deep=True)
            if group_id not in proposed.enable:
                proposed.enable.append(group_id)
            if group_id in proposed.disable:
                proposed.disable.remove(group_id)
            try:
                await writefile(guess_file, proposed.model_dump())
            except (ValueError, OSError) as exc:
                return f"群设置保存失败，未修改猜歌状态：{exc}"
            self.switch = proposed
        return "群猜歌功能已开启"

    async def off(self, group_id: str) -> str:
        """Persist group disablement before stopping its active game.

        Args:
            group_id: Group identifier.

        Returns:
            Confirmation or recovery instructions.
        """
        if not self.storage_available:
            return "猜歌群设置损坏，请管理员恢复 group_guess_switch.json 后重启插件。"
        async with _GROUP_SETTINGS_LOCK:
            proposed = self.switch.model_copy(deep=True)
            if group_id not in proposed.disable:
                proposed.disable.append(group_id)
            if group_id in proposed.enable:
                proposed.enable.remove(group_id)
            try:
                await writefile(guess_file, proposed.model_dump())
            except (ValueError, OSError) as exc:
                return f"群设置保存失败，未修改猜歌状态：{exc}"
            self.switch = proposed
            self.end(group_id)
        return "群猜歌功能已关闭"


guess = Guess()


class GroupAlias:

    push: AliasesPush

    @staticmethod
    def _normalize_group_ids(ids) -> List[str]:
        """群号统一为字符串，避免 int/str 混用导致 in 判断失效（见 GitHub issue #5）"""
        return [str(x) for x in ids if x is not None]

    def __init__(self) -> None:
        """Load notification restrictions, disabling broadcasts on damaged settings."""
        self.push = AliasesPush()
        self.storage_available = True
        try:
            if group_alias_file.exists():
                payload = read_json(group_alias_file, dict)
                for field in ("enable", "disable"):
                    if isinstance(payload.get(field), list):
                        payload[field] = self._normalize_group_ids(payload[field])
                self.push = AliasesPush.model_validate(payload)
        except (OSError, ValueError):
            self.storage_available = False
            try:
                if group_alias_file.exists():
                    preserve_corrupt(group_alias_file)
            except OSError:
                log.warning("Unable to create an alias settings backup")
            log.exception("Alias settings unavailable; broadcasts remain disabled")

    async def on(self, gid: str) -> str:
        """Persist notification enablement before applying the group change.

        Args:
            gid: Group identifier.

        Returns:
            Confirmation or recovery instructions.
        """
        if not self.storage_available:
            return "别名群设置损坏，请管理员恢复 group_alias_switch.json 后重启插件。"
        gid = str(gid)
        async with _GROUP_SETTINGS_LOCK:
            proposed = self.push.model_copy(deep=True)
            if gid not in proposed.enable:
                proposed.enable.append(gid)
            if gid in proposed.disable:
                proposed.disable.remove(gid)
            try:
                await writefile(group_alias_file, proposed.model_dump())
            except (ValueError, OSError) as exc:
                return f"群设置保存失败，未修改推送状态：{exc}"
            self.push = proposed
        return "群别名推送功能已开启"

    async def off(self, gid: str) -> str:
        """Persist notification disablement before applying the group change.

        Args:
            gid: Group identifier.

        Returns:
            Confirmation or recovery instructions.
        """
        if not self.storage_available:
            return "别名群设置损坏，请管理员恢复 group_alias_switch.json 后重启插件。"
        gid = str(gid)
        async with _GROUP_SETTINGS_LOCK:
            proposed = self.push.model_copy(deep=True)
            if gid not in proposed.disable:
                proposed.disable.append(gid)
            if gid in proposed.enable:
                proposed.enable.remove(gid)
            try:
                await writefile(group_alias_file, proposed.model_dump())
            except (ValueError, OSError) as exc:
                return f"群设置保存失败，未修改推送状态：{exc}"
            self.push = proposed
        return "群别名推送功能已关闭"

    async def alias_global_change(self, switch: bool, group_list: List[str]):
        """Persist a global notification change before updating active restrictions.

        Args:
            switch: Whether the listed groups should receive notifications.
            group_list: Group identifiers.

        Raises:
            ValueError: Existing group settings need administrator recovery.
            OSError: The change cannot be saved.
        """
        if not self.storage_available:
            raise ValueError(
                "Restore group_alias_switch.json before changing notifications"
            )
        async with _GROUP_SETTINGS_LOCK:
            groups = self._normalize_group_ids(group_list)
            proposed = AliasesPush(
                enable=groups if switch else [], disable=[] if switch else groups
            )
            await writefile(group_alias_file, proposed.model_dump())
            self.push = proposed


alias = GroupAlias()
