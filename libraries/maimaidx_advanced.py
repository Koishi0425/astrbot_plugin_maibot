"""Pure helpers for the extended query commands.

The functions in this module deliberately avoid AstrBot imports so they can be
unit-tested without a running bot.  They accept both pydantic records and plain
dictionaries returned by the public prober.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


DIFFICULTIES = ("Basic", "Advanced", "Expert", "Master", "Re:Master")
GRADE_NAMES = {
    1: "初段",
    2: "二段",
    3: "三段",
    4: "四段",
    5: "五段",
    6: "六段",
    7: "七段",
    8: "八段",
    9: "九段",
    10: "十段",
}


def field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def record_song_id(record: Any) -> int:
    value = field(record, "song_id", 0)
    return int(value or 0)


def record_level_index(record: Any) -> int:
    return int(field(record, "level_index", 0) or 0)


def is_ap(record: Any) -> bool:
    fc = str(field(record, "fc", "") or "").lower()
    return fc in {"ap", "app"}


def sorted_records(
    records: Iterable[Any],
    *,
    limit: int,
    predicate: Callable[[Any], bool] | None = None,
    key: Callable[[Any], Any] | None = None,
) -> list[Any]:
    selected = [record for record in records if predicate is None or predicate(record)]
    return sorted(
        selected,
        key=key
        or (
            lambda record: (
                int(field(record, "ra", 0) or 0),
                float(field(record, "achievements", 0) or 0),
            )
        ),
        reverse=True,
    )[:limit]


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


def course_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("courses"), dict):
        raise ValueError("段位目录缺少 courses")
    return payload
