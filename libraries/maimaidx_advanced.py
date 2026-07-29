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


def save_tech_music_cache(
    cache_file: Path,
    qqid: str,
    records: Sequence[Mapping[str, Any]],
    group_id: str,
) -> None:
    """Persist only sanitized records returned by the in-process Tech bridge."""
    cache: dict[str, Any] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    cache[str(qqid)] = {
        "source": "sdgbtech",
        "source_group": str(group_id),
        "records": [dict(record) for record in records],
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_sdgb_cache(cache_file: Path, qqid: str) -> dict[str, Any] | None:
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get(str(qqid))
    return value if isinstance(value, dict) else None


def build_diving_fish_records(
    records: Sequence[Mapping[str, Any]],
    music_by_id: Callable[[int], Any],
) -> tuple[list[dict[str, Any]], int]:
    """Convert sanitized SDGB records to Diving-Fish's import schema."""

    combo_status = {0: "", 1: "fc", 2: "fcp", 3: "ap", 4: "app"}
    sync_status = {
        0: "",
        1: "sync",
        2: "fs",
        3: "fsp",
        4: "fsd",
        5: "fsdp",
    }
    converted: list[dict[str, Any]] = []
    skipped = 0
    for record in records:
        try:
            if not {"musicId", "level", "achievement"} <= set(record):
                skipped += 1
                continue
            music_id = int(field(record, "musicId", 0) or 0)
            level_index = int(field(record, "level", -1))
            music = music_by_id(music_id)
            if music is None or level_index not in range(5):
                skipped += 1
                continue
            achievement = int(field(record, "achievement", 0) or 0) / 10000
            if not 0 <= achievement <= 101:
                skipped += 1
                continue
            converted.append({
                "achievements": achievement,
                "dxScore": max(0, int(field(record, "deluxscoreMax", 0) or 0)),
                "fc": combo_status.get(
                    int(field(record, "comboStatus", 0) or 0),
                    "",
                ),
                "fs": sync_status.get(
                    int(field(record, "syncStatus", 0) or 0),
                    "",
                ),
                "level_index": level_index,
                "title": str(field(music, "title", "") or ""),
                "type": str(field(music, "type", "") or "").upper(),
            })
        except (TypeError, ValueError):
            skipped += 1
    return converted, skipped


def course_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("courses"), dict):
        raise ValueError("段位目录缺少 courses")
    return payload
