"""Pure helpers for the extended query commands.

The functions in this module deliberately avoid AstrBot imports so they can be
unit-tested without a running bot.  They accept both pydantic records and plain
dictionaries (the latter is used by sdgb dumps).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


DIFFICULTIES = ("Basic", "Advanced", "Expert", "Master", "Re:Master")
GRADE_NAMES = {
    1: "初段", 2: "二段", 3: "三段", 4: "四段", 5: "五段",
    6: "六段", 7: "七段", 8: "八段", 9: "九段", 10: "十段",
}


def field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def record_song_id(record: Any) -> int:
    value = field(record, "song_id", field(record, "musicId", field(record, "id", 0)))
    return int(value or 0)


def record_level_index(record: Any) -> int:
    return int(field(record, "level_index", field(record, "level", 0)) or 0)


def is_ap(record: Any) -> bool:
    fc = str(field(record, "fc", "") or "").lower()
    if fc:
        return fc in {"ap", "app"}
    return int(field(record, "comboStatus", 0) or 0) >= 3


def sorted_records(
    records: Iterable[Any],
    *,
    limit: int,
    predicate: Callable[[Any], bool] | None = None,
    key: Callable[[Any], Any] | None = None,
) -> list[Any]:
    selected = [record for record in records if predicate is None or predicate(record)]
    if key is None:
        key = lambda record: (
            int(field(record, "ra", 0) or 0),
            float(field(record, "achievements", field(record, "achievement", 0)) or 0),
        )
    return sorted(selected, key=key, reverse=True)[:limit]


def chart_distribution(stats: Any) -> dict[str, float]:
    """Return FC/AP/player shares from a Diving-Fish chart_stats entry.

    ``fc_dist`` is ordered as no-FC, FC, FC+, AP, AP+.  Values may be counts or
    already-normalised weights, so percentages are always computed from the sum.
    The API does not expose all-chart player population.  ``play_share`` is thus
    intentionally left to the caller, which can compare ``cnt`` between the
    difficulties of the same song.
    """

    raw = list(field(stats, "fc_dist", []) or [])
    raw += [0.0] * (5 - len(raw))
    total = float(sum(float(value or 0) for value in raw[:5]))
    if total <= 0:
        return {"fc_rate": 0.0, "ap_rate": 0.0}
    fc = sum(float(value or 0) for value in raw[1:5])
    ap = sum(float(value or 0) for value in raw[3:5])
    return {"fc_rate": fc / total * 100, "ap_rate": ap / total * 100}


def flatten_sdgb_music(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in payload.get("userMusicList", []) or []:
        for detail in group.get("userMusicDetailList", []) or []:
            if isinstance(detail, Mapping):
                result.append(dict(detail))
    return result


def find_latest_sdgb_dump(root: Path, user_id: int) -> tuple[Path, list[dict[str, Any]]]:
    """Find the newest complete GetUserMusic dump for one exact game user id."""

    candidates: list[tuple[float, Path, Path]] = []
    if not root.exists():
        raise FileNotFoundError(f"sdgb dump 根目录不存在：{root}")
    for index_path in root.rglob("index.json"):
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if int(index.get("userId", 0) or 0) != int(user_id):
            continue
        files = index.get("files", {}) or {}
        names = [
            files.get("GetUserMusicApi"),
            "GetUserMusicApi_after_upsert.json",
            "GetUserMusicApi.json",
        ]
        music_path = next(
            (index_path.parent / name for name in names if name and (index_path.parent / name).exists()),
            None,
        )
        if music_path:
            candidates.append((music_path.stat().st_mtime, index_path, music_path))
    if not candidates:
        raise FileNotFoundError(f"没有找到 userId={user_id} 的 GetUserMusicApi dump")
    _, index_path, music_path = max(candidates, key=lambda item: item[0])
    payload = json.loads(music_path.read_text(encoding="utf-8"))
    return index_path.parent, flatten_sdgb_music(payload)


def save_sdgb_cache(cache_file: Path, qqid: str, user_id: int, dump_dir: Path, records: Sequence[Mapping[str, Any]]) -> None:
    cache: dict[str, Any] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    cache[str(qqid)] = {
        "userId": int(user_id),
        "dumpDir": str(dump_dir),
        "records": [dict(record) for record in records],
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sdgb_cache(cache_file: Path, qqid: str) -> dict[str, Any] | None:
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get(str(qqid))
    return value if isinstance(value, dict) else None


def parse_qq_user_mapping(raw: Any) -> dict[str, int]:
    if isinstance(raw, Mapping):
        source = raw
    elif isinstance(raw, str) and raw.strip():
        source = json.loads(raw)
    else:
        return {}
    result: dict[str, int] = {}
    for qqid, user_id in source.items():
        if str(qqid).isdigit() and str(user_id).isdigit():
            result[str(qqid)] = int(user_id)
    return result


def course_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("courses"), dict):
        raise ValueError("段位目录缺少 courses")
    return payload
