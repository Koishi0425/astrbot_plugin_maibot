"""Exact maimai DX scoring and BREAK judgement reconstruction."""

from dataclasses import dataclass
from fractions import Fraction


NOTE_TYPES = ("TAP", "HOLD", "SLIDE", "TOUCH", "BREAK")
NOTE_WEIGHTS = (500, 1000, 1500, 500, 2500)


@dataclass(frozen=True)
class Judgements:
    """Observed counts; critical=None means PERFECT includes CRITICAL PERFECT."""

    critical: int | None
    perfect: int
    great: int
    good: int
    miss: int

    @property
    def total(self) -> int:
        return (self.critical or 0) + self.perfect + self.great + self.good + self.miss


@dataclass(frozen=True, order=True)
class BreakDetails:
    """BREAK bins named by base score, rather than ambiguous Great numbering."""

    perfect_50: int = 0
    perfect_100: int = 0
    great_2000: int = 0
    great_1500: int = 0
    great_1250: int = 0


@dataclass
class ScoreAnalysis:
    status: str
    candidates: list[BreakDetails]
    dx_score: int | None
    max_dx_score: int
    reason: str = ""


def achievement_interval(
    display: str, *, rounded: bool = False
) -> tuple[Fraction, Fraction]:
    """Convert a displayed percentage into its exact half-open interval.

    Args:
        display: Displayed percentage, preserving all decimal places.
        rounded: Whether the result UI rounds instead of truncating (ADX).

    Returns:
        Inclusive lower and exclusive upper percentage bounds.

    Raises:
        ValueError: If the value or precision is invalid.
    """
    text = display.strip().removesuffix("%")
    places = len(text.partition(".")[2])
    value = Fraction(text)
    if not 0 <= value <= 101 or not 1 <= places <= 8:
        raise ValueError("Invalid achievement or precision")
    unit = Fraction(1, 10**places)
    return (value - unit / 2, value + unit / 2) if rounded else (value, value + unit)


def calculate_achievement(
    rows: dict[str, Judgements], details: BreakDetails
) -> Fraction:
    """Calculate the exact DX achievement percentage, including BREAK bonus.

    Args:
        rows: All five note rows, with explicit zero rows for absent note types.
        details: Counts of both BREAK PERFECT and all three BREAK GREAT bins.

    Returns:
        Exact achievement percentage before display truncation.

    Raises:
        ValueError: If counts are negative, totals disagree, or the chart is empty.
    """
    if set(rows) != set(NOTE_TYPES):
        raise ValueError("All note rows are required")
    for row in rows.values():
        if any(value < 0 for value in vars(row).values() if value is not None):
            raise ValueError("Negative judgement count")
    if any(value < 0 for value in vars(details).values()):
        raise ValueError("Negative BREAK count")
    brk = rows["BREAK"]
    small_p = details.perfect_50 + details.perfect_100
    if (
        sum((details.great_2000, details.great_1500, details.great_1250)) != brk.great
        or (brk.critical is not None and small_p != brk.perfect)
        or small_p > brk.perfect
    ):
        raise ValueError("BREAK bins do not match the observed row")
    maximum = sum(
        rows[name].total * weight for name, weight in zip(NOTE_TYPES, NOTE_WEIGHTS)
    )
    if maximum == 0:
        raise ValueError("Empty chart")
    loss = sum(
        weight * row.great // 5 + weight * row.good // 2 + weight * row.miss
        for name, weight in zip(NOTE_TYPES[:-1], NOTE_WEIGHTS[:-1])
        for row in (rows[name],)
    )
    loss += (
        500 * details.great_2000
        + 1000 * details.great_1500
        + 1250 * details.great_1250
        + 1500 * brk.good
        + 2500 * brk.miss
    )
    base = Fraction(100 * (maximum - loss), maximum)
    if not brk.total:
        return base
    bonus_loss = (
        25 * details.perfect_50
        + 50 * details.perfect_100
        + 60 * brk.great
        + 70 * brk.good
        + 100 * brk.miss
    )
    return base + Fraction(100 * brk.total - bonus_loss, 100 * brk.total)


def analyse_judgements(
    rows: dict[str, Judgements],
    achievement: str | None,
    dx_score: int | None = None,
    expected_notes: tuple[int, ...] | None = None,
    max_dx_score: int | None = None,
    *,
    rounded: bool = False,
) -> ScoreAnalysis:
    """Validate a complete table and exhaustively reconstruct compatible BREAK bins.

    Args:
        rows: Complete note rows with five or four judgement columns.
        achievement: Displayed achievement percentage, or None if unreadable.
        dx_score: Observed DX score, if present.
        expected_notes: Optional chart totals ordered as NOTE_TYPES.
        max_dx_score: Observed maximum DX score, if present.
        rounded: Whether achievement uses ADX rounding rather than truncation.

    Returns:
        Unique, ambiguous, insufficient, or inconsistent analysis. No guessed bins.
    """
    maximum_dx = 3 * sum(row.total for row in rows.values())
    result = ScoreAnalysis("inconsistent", [], None, maximum_dx)
    if set(rows) != set(NOTE_TYPES):
        result.status = "insufficient"
        result.reason = "判定表不完整，无法计算绝赞细分。"
        return result
    if any(
        value < 0
        for row in rows.values()
        for value in vars(row).values()
        if value is not None
    ):
        result.reason = "判定数量不能为负数。"
        return result
    if not maximum_dx:
        result.reason = "判定表为空。"
        return result
    totals = tuple(rows[name].total for name in NOTE_TYPES)
    if expected_notes is not None and totals != expected_notes:
        result.reason = "判定总数与谱面物量不一致，请检查图片清晰度或歌曲匹配。"
        return result
    if max_dx_score is not None and max_dx_score != maximum_dx:
        result.reason = "DX 分上限与判定总数不一致。"
        return result
    explicit_critical = all(row.critical is not None for row in rows.values())
    if not explicit_critical and any(row.critical is not None for row in rows.values()):
        result.status = "insufficient"
        result.reason = "判定列格式不一致，请发送完整表格。"
        return result
    perfect_total = sum(row.perfect for row in rows.values())
    critical_total = sum(row.critical or 0 for row in rows.values())
    great_total = sum(row.great for row in rows.values())
    if explicit_critical:
        result.dx_score = critical_total * 3 + perfect_total * 2 + great_total
        if dx_score is not None and dx_score != result.dx_score:
            result.reason = "DX 分与判定详情不一致。"
            return result
    elif dx_score is not None:
        critical_total = dx_score - 2 * perfect_total - great_total
        if not 0 <= critical_total <= perfect_total:
            result.reason = "DX 分与合并的 PERFECT 数量不一致。"
            return result
        result.dx_score = dx_score
    brk = rows["BREAK"]
    p_min = brk.perfect if brk.critical is not None else 0
    p_max = brk.perfect
    if brk.critical is None and dx_score is not None:
        p_min = max(0, brk.perfect - critical_total)
        p_max = min(brk.perfect, perfect_total - critical_total)
    if achievement is None and (p_max or brk.great):
        result.status = "insufficient"
        result.reason = "未识别到达成率，无法区分绝赞小 P 与 Great 档位。"
        return result
    try:
        bounds = (
            achievement_interval(achievement, rounded=rounded) if achievement else None
        )
    except ValueError:
        result.reason = "达成率格式无效。"
        return result
    # Work is bounded, but reaching the bound must never imply a unique solution.
    combinations = (brk.great + 1) * (brk.great + 2) // 2 * (p_max - p_min + 1)
    if combinations > 200_000:
        return ScoreAnalysis(
            "insufficient",
            [],
            result.dx_score,
            maximum_dx,
            "绝赞候选过多，暂时无法完成精确细分。",
        )
    work = 0
    for g2000 in range(brk.great + 1):
        for g1500 in range(brk.great - g2000 + 1):
            g1250 = brk.great - g2000 - g1500
            for perfect in range(p_min, p_max + 1):
                work += 1
                if work > 200_000:
                    return ScoreAnalysis(
                        "insufficient",
                        [],
                        result.dx_score,
                        maximum_dx,
                        "绝赞候选过多，暂时无法完成精确细分。",
                    )
                details = BreakDetails(0, perfect, g2000, g1500, g1250)
                base = calculate_achievement(rows, details)
                if bounds is None or not brk.total:
                    candidates = (0,)
                else:
                    # Replacing P100 with P50 increases the percentage by 1/(4*B).
                    lower = (bounds[0] - base) * (4 * brk.total)
                    upper = (bounds[1] - base) * (4 * brk.total)
                    start = max(0, -(-lower.numerator // lower.denominator))
                    stop = min(perfect, -(-upper.numerator // upper.denominator) - 1)
                    candidates = range(start, stop + 1)
                for p50 in candidates:
                    candidate = BreakDetails(p50, perfect - p50, g2000, g1500, g1250)
                    value = base + (Fraction(p50, 4 * brk.total) if brk.total else 0)
                    if bounds is None or bounds[0] <= value < bounds[1]:
                        result.candidates.append(candidate)
                        if len(result.candidates) > 20_000:
                            return ScoreAnalysis(
                                "insufficient",
                                [],
                                result.dx_score,
                                maximum_dx,
                                "截图精度不足，绝赞候选过多。",
                            )
    if result.candidates:
        result.status = "unique" if len(result.candidates) == 1 else "ambiguous"
    else:
        result.reason = "达成率与判定详情无法同时满足舞萌 DX 计分规则。"
    return result
