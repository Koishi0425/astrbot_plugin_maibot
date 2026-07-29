"""Formatting and strict argument helpers for the generic private backend."""

from __future__ import annotations

import re
from typing import Any


CAPABILITY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
VALUE_RE = re.compile(r"[A-Za-z0-9_.:+-]{1,64}")


def parse_parameters(text: str) -> dict[str, str]:
    normalized = str(text or "").strip()
    if not normalized or normalized == "-":
        return {}
    if len(normalized) > 256:
        raise ValueError("参数过长")
    result: dict[str, str] = {}
    for token in normalized.split(","):
        if "=" not in token:
            raise ValueError("参数必须使用 key=value，并以逗号分隔")
        key, value = (part.strip() for part in token.split("=", 1))
        if not KEY_RE.fullmatch(key) or not VALUE_RE.fullmatch(value):
            raise ValueError("参数包含不允许的字符")
        if key in result:
            raise ValueError(f"参数 {key} 不能重复")
        result[key] = value
    return result


def validate_capability(value: str) -> str:
    normalized = str(value).strip()
    if not CAPABILITY_RE.fullmatch(normalized):
        raise ValueError("能力名称格式无效")
    return normalized


def validate_identifier(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{label}格式无效")
    return normalized


def format_capabilities(items: list[dict[str, Any]]) -> str:
    rows = ["私有 SDGB Tech 能力："]
    for item in items:
        mode = "写入" if item.get("write") else "只读"
        required = ",".join(item.get("required", [])) or "无"
        optional = sorted(
            set(item.get("arguments", [])) - set(item.get("required", []))
        )
        suffix = f"；可选={','.join(optional)}" if optional else ""
        rows.append(
            f"- {item.get('name')}：{item.get('title')} [{mode}]；"
            f"必填={required}{suffix}"
        )
    if len(rows) == 1:
        rows.append("- 当前没有可用能力")
    rows.append("参数格式：key=value,key=value；无参数使用 -")
    return "\n".join(rows)


async def draw_identity_b50(identity: dict[str, Any], qqid: str):
    """Render no-login GetUserRating B35/B15 data with maibot assets."""
    from .. import MessageSegment
    from ..libraries.image import image_to_base64
    from ..libraries.maimai_best_50 import DrawBest, computeRa
    from ..libraries.maimaidx_model import ChartInfo, Data, UserInfo
    from ..libraries.maimaidx_music import mai

    fc_names = {1: "fc", 2: "fcp", 3: "ap", 4: "app"}
    fs_names = {
        1: "sync", 2: "fs", 3: "fsp", 4: "fsd", 5: "fsdp",
    }

    def convert(rows: list[dict[str, Any]]) -> list[ChartInfo]:
        result = []
        for row in rows:
            song_id = int(row.get("musicId", 0) or 0)
            level_index = int(row.get("level", -1))
            music = mai.total_list.by_id(str(song_id))
            if (
                music is None
                or level_index < 0
                or level_index >= len(music.ds)
            ):
                continue
            achievement = float(row.get("achievement", 0) or 0)
            if achievement > 1000:
                achievement /= 10000
            ra, rate = computeRa(
                float(music.ds[level_index]),
                achievement,
                israte=True,
            )
            result.append(ChartInfo(
                achievements=achievement,
                fc=fc_names.get(int(row.get("comboStatus", 0) or 0), ""),
                fs=fs_names.get(int(row.get("syncStatus", 0) or 0), ""),
                level=str(music.level[level_index]),
                level_index=level_index,
                level_label=("Basic", "Advanced", "Expert", "Master", "Re:Master")[
                    level_index
                ],
                title=music.title,
                type=music.type,
                ds=float(music.ds[level_index]),
                dxScore=int(row.get("deluxscoreMax", 0) or 0),
                ra=ra,
                rate=rate,
                song_id=song_id,
            ))
        return result

    user = UserInfo(
        additional_rating=max(
            0,
            min(int(identity.get("additional_rating", 0) or 0), 30),
        ),
        nickname=str(identity.get("nickname") or "未知玩家")[:32],
        plate=None,
        rating=int(identity.get("rating", 0) or 0),
        username=None,
        charts=Data(
            sd=convert(list(identity.get("old_best", []) or [])),
            dx=convert(list(identity.get("new_best", []) or [])),
        ),
    )
    image = await DrawBest(user, qqid).draw()
    return MessageSegment.image(image_to_base64(image))
