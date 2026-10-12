"""Parse editable score commands without guessing missing judgement counts."""

import re

from .maimaidx_score import NOTE_TYPES, Judgements, analyse_judgements
from .maimaidx_score_recognition import DIFFICULTIES, RecognizedScore


def parse_manual_score(text: str, musics: list) -> RecognizedScore:
    """Read a manual score, match its chart and validate its judgement table.

    Args:
        text: Command parameters such as id=11167 ach=100.2098 tap=453/226/17/0/1.
        musics: Public music catalog used to resolve chart metadata and note totals.

    Returns:
        Recognized score with missing values preserved and verified BREAK details.

    Raises:
        ValueError: Parameters are duplicated, unsupported or malformed.
    """
    result = RecognizedScore("manual")
    fields = {}
    allowed = {
        "id",
        "ach",
        "diff",
        "dx",
        "round",
        *(name.lower() for name in NOTE_TYPES),
    }
    for token in text.split():
        key, separator, value = token.partition("=")
        key = key.lower()
        if not separator or key not in allowed or not value or key in fields:
            raise ValueError(
                "参数格式错误：每项使用 名称=值，未知信息用 ?，判定用 / 分隔。"
            )
        fields[key] = value
    if not fields:
        raise ValueError("请附上结算图片，或填写 id=… ach=… 和各键型判定。")
    identifier = fields.get("id", "?")
    if identifier != "?":
        if not re.fullmatch(r"[0-9]{1,6}", identifier):
            raise ValueError("id 应为歌曲 ID 或 ?。")
        result.music_id = str(int(identifier))
    achievement = fields.get("ach", "?").removesuffix("%")
    if achievement != "?":
        if (
            not re.fullmatch(r"(?:[0-9]{1,3})\.[0-9]{1,8}", achievement)
            or not 0 <= float(achievement) <= 101
        ):
            raise ValueError("ach 应为 0～101 之间、保留小数位的达成率或 ?。")
        result.achievement = achievement
    difficulty = fields.get("diff", "?")
    if difficulty != "?":
        aliases = {name.upper(): index for index, name in enumerate(DIFFICULTIES)}
        aliases.update(
            dict(
                zip(
                    ("绿", "黄", "红", "紫", "白", "0", "1", "2", "3", "4"),
                    (0, 1, 2, 3, 4, 0, 1, 2, 3, 4),
                )
            )
        )
        if difficulty.upper() not in aliases:
            raise ValueError(
                "diff 应为 Basic/Advanced/Expert/Master/Re:Master 或绿黄红紫白。"
            )
        result.difficulty = aliases[difficulty.upper()]
    dx = fields.get("dx", "?")
    if dx != "?":
        if not re.fullmatch(r"[0-9]{1,7}(?:/[0-9]{1,7})?", dx):
            raise ValueError("dx 应为 DX 分、DX分/上限 或 ?。")
        values = dx.split("/")
        result.dx_score = int(values[0])
        result.max_dx_score = int(values[1]) if len(values) == 2 else None
    rounding = fields.get("round", "0")
    if rounding not in ("0", "1"):
        raise ValueError("round 应为 0（截断）或 1（ADX 四舍五入）。")
    if rounding == "1":
        result.layout = "adx_manual"
    merged = False
    for name in NOTE_TYPES:
        values = fields.get(name.lower(), "?/?/?/?/?").split("/")
        if len(values) not in (4, 5):
            raise ValueError(
                f"{name.lower()} 需要 CP/P/Great/Good/Miss 五项；合并 P 的表格也可填写 P/Great/Good/Miss 四项。"
            )
        if any(
            value != "?" and not re.fullmatch(r"[0-9]{1,6}", value) for value in values
        ):
            raise ValueError(f"{name.lower()} 的判定应为非负整数或 ?。")
        cells = [None if value == "?" else int(value) for value in values]
        if len(cells) == 4:
            merged = True
            cells.insert(0, None)
        result.partial_rows[name] = cells
    if merged and any(
        len(fields.get(name.lower(), "?/?/?/?").split("/")) != 4 for name in NOTE_TYPES
    ):
        raise ValueError("请统一使用五项判定，或统一使用含 CP 的四项合并 P 判定。")
    result.merged_perfect = merged
    complete_totals = {
        name: sum(value for value in cells if value is not None)
        for name, cells in result.partial_rows.items()
        if all(value is not None for value in cells[1 if merged else 0 :])
    }
    candidates = []
    for music in musics:
        if result.music_id and str(music.id) != result.music_id:
            continue
        for index, chart in enumerate(music.charts):
            if result.difficulty is not None and index != result.difficulty:
                continue
            notes = tuple(chart.notes)
            if len(notes) == 4:
                notes = (*notes[:3], 0, notes[3])
            if len(notes) == 5 and all(
                notes[NOTE_TYPES.index(name)] == total
                for name, total in complete_totals.items()
            ):
                candidates.append((music, index, notes))
    expected_notes = None
    if len(candidates) == 1:
        music, result.difficulty, expected_notes = candidates[0]
        result.music_id, result.title = str(music.id), music.title
    elif candidates:
        result.warnings.append("请填写歌曲 id 或补充 diff 以确定谱面。")
    elif result.music_id:
        result.warnings.append(
            "歌曲 ID 不在当前曲库中，或判定数量与该歌曲谱面不一致，请核对。"
        )
    for name, cells in result.partial_rows.items():
        missing = [
            index for index in range(1 if merged else 0, 5) if cells[index] is None
        ]
        if len(missing) == 1 and expected_notes is not None:
            remainder = expected_notes[NOTE_TYPES.index(name)] - sum(
                value for value in cells if value is not None
            )
            if remainder >= 0:
                cells[missing[0]] = remainder
        if all(value is not None for value in cells[1 if merged else 0 :]):
            result.rows[name] = Judgements(*cells)
    result.analysis = analyse_judgements(
        result.rows,
        result.achievement,
        result.dx_score,
        expected_notes,
        result.max_dx_score,
        rounded=rounding == "1",
    )
    return result
