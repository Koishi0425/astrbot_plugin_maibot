"""Local OCR for cabinet photos, official result pages, and ADX results.

The table-layout approach follows maimai-score-studio. Missing cells and
non-unique BREAK solutions are kept explicit instead of filled with guesses.
"""

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
import re
import unicodedata

from .maimaidx_score import NOTE_TYPES, Judgements, ScoreAnalysis, analyse_judgements


DIFFICULTIES = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER")
HEADERS = ("CRITICAL", "PERFECT", "GREAT", "GOOD", "MISS")
ROW_ALIASES = {
    "TAP": ("TAP", "点击", "点击", "轻触"),
    "HOLD": ("HOLD", "长按"),
    "SLIDE": ("SLIDE", "滑动"),
    "TOUCH": ("TOUCH", "触摸"),
    "BREAK": ("BREAK", "绝赞"),
}
HEADER_ALIASES = (
    ("CRITICAL", "CRIT", "CP", "绝妙"),
    ("PERFECT", "PERF", "P", "完美"),
    ("GREAT", "极好"),
    ("GOOD", "良好"),
    ("MISS", "失误", "错误"),
)


def plain(text: str) -> str:
    """Normalize OCR labels without discarding Japanese characters.

    Args:
        text: OCR label or song title.

    Returns:
        Uppercase letters and numbers after Unicode normalization.
    """
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).upper() if char.isalnum()
    )


def integer(text: str) -> int | None:
    """Read a nonnegative counter without coercing unrelated OCR text.

    Args:
        text: OCR token containing a counter.

    Returns:
        Counter, or None when the token is not a counter.
    """
    value = (
        unicodedata.normalize("NFKC", text).strip().replace(",", "").replace("，", "")
    )
    value = value.replace("O", "0").replace("o", "0")
    return int(value) if re.fullmatch(r"[0-9]{1,6}", value) else None


@dataclass(frozen=True)
class OCRToken:
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float
    slope: float = 0

    @classmethod
    def from_box(cls, text, score, box, scale=1, offset=(0, 0)):
        """Preserve the text angle while mapping an OCR crop to source coordinates.

        Args:
            text: Recognized text.
            score: OCR confidence.
            box: Four ordered corners returned by the detector.
            scale: Enlargement applied before OCR.
            offset: Source coordinates of the crop's upper-left corner.

        Returns:
            Token with source bounds and the slope of its horizontal edges.
        """
        return cls(
            str(text),
            float(score),
            float(box[:, 0].min()) / scale + offset[0],
            float(box[:, 1].min()) / scale + offset[1],
            float(box[:, 0].max()) / scale + offset[0],
            float(box[:, 1].max()) / scale + offset[1],
            float(box[1, 1] - box[0, 1] + box[2, 1] - box[3, 1])
            / max(1, float(box[1, 0] - box[0, 0] + box[2, 0] - box[3, 0])),
        )

    @property
    def x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def area(self) -> float:
        return (self.right - self.left) * (self.bottom - self.top)


@dataclass
class JudgementTable:
    cells: dict[str, list[int | None]] = field(default_factory=dict)
    merged: bool = False
    centers: dict[tuple[str, int], tuple[float, float]] = field(default_factory=dict)
    column_step: float = 0
    row_step: float = 0


@dataclass
class RecognizedScore:
    layout: str
    achievement: str | None = None
    dx_score: int | None = None
    max_dx_score: int | None = None
    music_id: str | None = None
    title: str | None = None
    difficulty: int | None = None
    rows: dict[str, Judgements] = field(default_factory=dict)
    partial_rows: dict[str, list[int | None]] = field(default_factory=dict)
    merged_perfect: bool = False
    aggregate: Judgements | None = None
    analysis: ScoreAnalysis | None = None
    warnings: list[str] = field(default_factory=list)

    def format(self) -> str:
        """Render observed data and verified BREAK solutions for a chat reply.

        Returns:
            Plain-text score report.
        """
        lines = ["mai识分"]
        if self.music_id:
            difficulty = (
                DIFFICULTIES[self.difficulty]
                if self.difficulty is not None
                else "难度未知"
            )
            lines.append(f"{self.title} · {difficulty} · ID {self.music_id}")
        else:
            lines.append("歌曲 ID 未匹配")
        if self.achievement:
            lines.append(f"达成率：{self.achievement}%")
        if self.dx_score is not None:
            suffix = f" / {self.max_dx_score}" if self.max_dx_score is not None else ""
            lines.append(f"DX 分：{self.dx_score}{suffix}")
        if self.rows:
            merged = any(row.critical is None for row in self.rows.values())
            lines.append(
                "判定（P / Great / Good / Miss）"
                if merged
                else "判定（CP / P / Great / Good / Miss）"
            )
            for name in NOTE_TYPES:
                row = self.rows.get(name)
                if row is None:
                    lines.append(f"{name}：未完整识别")
                    continue
                values = [row.perfect, row.great, row.good, row.miss]
                if not merged:
                    values.insert(0, row.critical)
                lines.append(f"{name}：" + " / ".join(str(value) for value in values))
        if self.aggregate is not None:
            row = self.aggregate
            lines.append(
                f"汇总 CP / P / Great / Good / Miss：{row.critical} / {row.perfect} / {row.great} / {row.good} / {row.miss}"
            )
            lines.append("简略结算缺少各键型判定，请发 Details 详细页以计算绝赞细分。")
        analysis = self.analysis
        if analysis and analysis.status in ("unique", "ambiguous"):
            candidates = analysis.candidates
            if analysis.status == "unique":
                details = candidates[0]
                lines.append(
                    f"绝赞小 P：{details.perfect_50 + details.perfect_100}（50 落 {details.perfect_50}，100 落 {details.perfect_100}）"
                )
                lines.append(
                    f"绝赞 Great：2000 档 {details.great_2000}，1500 档 {details.great_1500}，1250 档 {details.great_1250}"
                )
                lines.append("校验通过，绝赞细分唯一。")
            else:
                small_p_values = [
                    candidate.perfect_50 + candidate.perfect_100
                    for candidate in candidates
                ]
                lower, upper = min(small_p_values), max(small_p_values)
                lines.append(
                    f"绝赞小 P：{lower}"
                    if lower == upper
                    else f"绝赞小 P：{lower}～{upper}"
                )
                lines.append(f"校验通过，绝赞细分有 {len(candidates)} 组候选：")
                for label, attribute in (
                    ("50 落", "perfect_50"),
                    ("100 落", "perfect_100"),
                    ("Great 2000", "great_2000"),
                    ("Great 1500", "great_1500"),
                    ("Great 1250", "great_1250"),
                ):
                    values = [getattr(candidate, attribute) for candidate in candidates]
                    lower, upper = min(values), max(values)
                    lines.append(
                        f"{label}：{lower}"
                        if lower == upper
                        else f"{label}：{lower}～{upper}"
                    )
        elif analysis:
            lines.append(analysis.reason)
        low_confidence = [
            warning for warning in self.warnings if "识别置信度较低" in warning
        ]
        if low_confidence:
            lines.append("提示：部分文字识别置信度较低，请核对判定与达成率。")
        lines.extend(
            f"提示：{warning}"
            for warning in dict.fromkeys(self.warnings)
            if warning not in low_confidence
        )
        return "\n".join(lines)


def parse_table(tokens: list[OCRToken], warnings: list[str]) -> JudgementTable:
    """Locate a four/five-column judgement table using label geometry.

    Args:
        tokens: OCR tokens in original image coordinates.
        warnings: Recoveries or low-confidence observations to report.

    Returns:
        Row cells, column mode, and cell geometry. Unreadable cells remain None,
        never an assumed zero.
    """
    row_labels = [
        token
        for token in tokens
        if any(
            plain(token.text).startswith(alias)
            for aliases in ROW_ALIASES.values()
            for alias in aliases
        )
    ]
    slope = median(token.slope for token in row_labels) if row_labels else 0
    # Compare rows in a deskewed coordinate system. A cabinet photo's rightmost
    # cell can sit more than one row below its label in the original image.
    tokens = [
        replace(
            token,
            top=token.top - slope * token.x,
            bottom=token.bottom - slope * token.x,
        )
        for token in tokens
    ]
    anchors = {}
    for name, aliases in ROW_ALIASES.items():
        hits = [
            token
            for token in tokens
            if any(plain(token.text).startswith(alias) for alias in aliases)
        ]
        if hits:
            anchors[name] = max(hits, key=lambda token: token.score)
    if len(anchors) < 3:
        return JudgementTable()
    ordered = sorted(anchors.items(), key=lambda pair: pair[1].y)
    spacing = median(
        right.y - left.y for (_, left), (_, right) in zip(ordered, ordered[1:])
    )
    if spacing <= 0:
        return JudgementTable()
    first_y = ordered[0][1].y
    header_hits = {}
    for column, aliases in enumerate(HEADER_ALIASES):
        hits = [
            token
            for token in tokens
            if first_y - 3 * spacing < token.y < first_y - spacing * 0.25
            and any(plain(token.text).startswith(alias) for alias in aliases)
        ]
        if hits:
            header_hits[column] = max(hits, key=lambda token: token.score).x
    # CRITICAL PERFECT may be printed on two lines above the same column.
    if (
        0 in header_hits
        and 1 in header_hits
        and abs(header_hits[0] - header_hits[1]) < spacing
    ):
        hits = [
            token
            for token in tokens
            if first_y - 3 * spacing < token.y < first_y - spacing * 0.25
            and plain(token.text).startswith(("PERFECT", "PERF"))
            and token.x > header_hits[0] + spacing
        ]
        if hits:
            header_hits[1] = max(hits, key=lambda token: token.score).x
        else:
            header_hits.pop(1)
    merged = 0 not in header_hits
    columns = tuple(range(1 if merged else 0, 5))
    slopes = [
        (header_hits[b] - header_hits[a]) / (b - a)
        for a in columns
        for b in columns
        if a < b and a in header_hits and b in header_hits
    ]
    numbers = []
    for token in tokens:
        value = integer(token.text)
        if value is not None:
            numbers.append((token, value))
        elif re.fullmatch(r"\s*[0-9]+(?:\s+[0-9]+){1,4}\s*", token.text):
            # OCR sometimes merges adjacent numeric cells into one text box.
            for match in re.finditer(r"[0-9]+", token.text):
                width = (token.right - token.left) / len(token.text)
                cell = OCRToken(
                    match.group(),
                    token.score,
                    token.left + match.start() * width,
                    token.top,
                    token.left + match.end() * width,
                    token.bottom,
                )
                numbers.append((cell, int(match.group())))
    if slopes:
        step = median(slopes)
        origin = median(x - column * step for column, x in header_hits.items())
    if merged or not slopes:
        # Glare can erase the colored headers while leaving five numeric cells
        # readable. Require five distinct evenly spaced numeric columns, never assume
        # that four visible columns include a separate CRITICAL count.
        candidate_rows = []
        for anchor in anchors.values():
            row = sorted(
                (
                    token.x
                    for token, _ in numbers
                    if token.left > anchor.right
                    and abs(token.y - anchor.y) < spacing * 0.43
                )
            )[:5]
            candidate_rows.append(row)
        # Tiny zeroes can be missed in different rows. Cluster the observed
        # positions across the table without inventing any cell values.
        clusters = []
        for x in sorted(
            token.x
            for token, _ in numbers
            if token.left > max(anchor.right for anchor in anchors.values())
            and first_y - spacing * 0.43
            < token.y
            < max(anchor.y for anchor in anchors.values()) + spacing * 1.5
        ):
            if clusters and x - median(clusters[-1]) < spacing * 0.8:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        if len(clusters) >= 5:
            candidate_rows.append([median(cluster) for cluster in clusters[:5]])
        grids = []
        for row in candidate_rows:
            if len(row) == 5:
                gaps = [b - a for a, b in zip(row, row[1:])]
                width = median(gaps)
                if width > 0 and all(0.65 * width < gap < 1.35 * width for gap in gaps):
                    if (
                        4 not in header_hits
                        or abs(row[4] - header_hits[4]) < width * 0.6
                    ):
                        grids.append(row)
        if not grids and not slopes:
            return JudgementTable()
        if grids:
            header_hits = {
                column: median(row[column] for row in grids) for column in range(5)
            }
            origin = header_hits[0]
            step = median(
                header_hits[b] - header_hits[a] for a, b in zip(range(4), range(1, 5))
            )
            merged = False
            columns = tuple(range(5))
            warnings.append("表头未完整识别，根据五列判定位置恢复。")
    if step <= 0:
        return JudgementTable()
    if merged:
        critical_cells = [
            token
            for token, _ in numbers
            if abs(token.x - origin) < step * 0.43
            and any(
                abs(token.y - anchor.y) < spacing * 0.43 for anchor in anchors.values()
            )
        ]
        if len(critical_cells) >= 2:
            merged = False
            columns = tuple(range(5))
            warnings.append("CRITICAL 表头未识别，根据五列表格位置恢复。")
    # A missed label can be restored only if numeric cells occupy its predicted row.
    if len(anchors) == 4:
        positions = {NOTE_TYPES.index(name): token.y for name, token in anchors.items()}
        slopes_y = [
            (positions[b] - positions[a]) / (b - a)
            for a in positions
            for b in positions
            if a < b
        ]
        row_step = median(slopes_y)
        row_origin = median(y - index * row_step for index, y in positions.items())
        missing = next(name for name in NOTE_TYPES if name not in anchors)
        y = row_origin + NOTE_TYPES.index(missing) * row_step
        nearby = [
            token
            for token, _ in numbers
            if abs(token.y - y) < row_step * 0.4
            and origin + (columns[0] - 0.5) * step < token.x < origin + 4.5 * step
        ]
        observed_columns = {
            column
            for column in columns
            if any(
                abs(token.x - (origin + column * step)) < step * 0.43
                for token in nearby
            )
        }
        if len(observed_columns) >= 3 and all(
            abs(token.y - y) > row_step * 0.5 for token in anchors.values()
        ):
            anchors[missing] = OCRToken(missing, 1, 0, y - 1, 0, y + 1)
            warnings.append(f"{missing} 标签未识别，根据表格行位置恢复。")
    result = JudgementTable(merged=merged, column_step=step, row_step=spacing)
    for name, anchor in anchors.items():
        cells = [None] * 5
        for column in columns:
            x = header_hits.get(column, origin + column * step)
            result.centers[name, column] = (x, anchor.y + slope * x)
            hits = [
                (token, value)
                for token, value in numbers
                if abs(token.y - anchor.y) < spacing * 0.43
                and abs(token.x - x) < step * 0.43
            ]
            if hits:
                token, value = min(
                    hits,
                    key=lambda pair: abs(pair[0].x - x) + abs(pair[0].y - anchor.y),
                )
                cells[column] = value
                if token.score < 0.8:
                    warnings.append(
                        f"{name} {HEADERS[column]} 识别置信度较低，请核对。"
                    )
        result.cells[name] = cells
    return result


def parse_ocr_tokens(tokens: list[OCRToken], musics: list) -> RecognizedScore:
    """Extract result fields, match chart IDs, and validate exact DX scoring.

    Args:
        tokens: OCR text and bounding boxes.
        musics: Public maibot Music models with titles and chart note counts.

    Returns:
        Observed fields, warnings, and optional BREAK reconstruction.
    """
    labels = {plain(token.text) for token in tokens}
    summary = any(label in ("GRADING", "FLUCTUATION") for label in labels)
    adx = (
        summary
        or len(labels.intersection({"JUDGEMENT", "MAXCOMBO", "OFFSET", "CLEARS"})) >= 2
        or {"CRIT", "PERF"}.issubset(labels)
    )
    result = RecognizedScore(
        "adx_summary" if summary else "adx_details" if adx else "details"
    )
    percentage_tokens = tokens.copy()
    for suffix in tokens:
        text = unicodedata.normalize("NFKC", suffix.text).strip()
        if not re.fullmatch(r"\.?[0-9]{1,8}%", text):
            continue
        prefixes = [
            prefix
            for prefix in tokens
            if re.fullmatch(
                r"[0-9]{1,3}\.?", unicodedata.normalize("NFKC", prefix.text).strip()
            )
            and (prefix.text.endswith(".") or text.startswith("."))
            and -0.6 * (suffix.bottom - suffix.top)
            <= suffix.left - prefix.right
            < 0.8 * max(prefix.bottom - prefix.top, suffix.bottom - suffix.top)
            and abs(prefix.y - suffix.y)
            < 0.35 * max(prefix.bottom - prefix.top, suffix.bottom - suffix.top)
        ]
        if len(prefixes) == 1:
            prefix = prefixes[0]
            percentage_tokens.append(
                OCRToken(
                    unicodedata.normalize("NFKC", prefix.text).strip().rstrip(".")
                    + "."
                    + text.lstrip("."),
                    min(prefix.score, suffix.score),
                    prefix.left,
                    min(prefix.top, suffix.top),
                    suffix.right,
                    max(prefix.bottom, suffix.bottom),
                )
            )
    achievement_labels = [
        token for token in tokens if plain(token.text) in ("达成率", "ACHIEVEMENT")
    ]
    percentages = []
    for token in percentage_tokens:
        current_region = any(
            label.left - 3 * (label.right - label.left)
            < token.x
            < label.right + 8 * (label.right - label.left)
            and label.bottom
            < token.y
            < label.bottom
            + max(6 * (label.bottom - label.top), 2 * (label.right - label.left))
            for label in achievement_labels
        )
        if achievement_labels and not current_region:
            continue
        text = unicodedata.normalize("NFKC", token.text).strip()
        if current_region and re.fullmatch(r"[0-9]{1,3}\.[0-9]{4,8}", text):
            text += "%"
        for match in re.finditer(
            r"(?<![\d.])([0-9]{1,3}\.[0-9]{1,8})\s*[%％]",
            text,
        ):
            value = match.group(1)
            if float(value) <= 101:
                percentages.append((token.area, float(value), value, token.score))
    if percentages:
        area, _, result.achievement, confidence = max(percentages)
        if any(
            re.fullmatch(r"\.[0-9]{1,8}[%％]", token.text.strip()) and token.area > area
            for token in tokens
        ):
            # A larger incomplete current score takes precedence over a small
            # complete personal best; let the recognizer reread its pixels.
            result.achievement = None
        if confidence < 0.8:
            result.warnings.append("达成率识别置信度较低，请核对。")
    difficulty_hits = []
    for token in tokens:
        text = plain(token.text)
        for index, difficulty in reversed(tuple(enumerate(DIFFICULTIES))):
            if plain(difficulty) in text:
                difficulty_hits.append((token.score, index))
                break
    if difficulty_hits:
        result.difficulty = max(difficulty_hits)[1]
    dx_labels = [
        token
        for token in tokens
        if plain(token.text).startswith(("DXSCORE", "DX分数", "DX分"))
    ]
    fractions = []
    for token in tokens:
        text = token.text.replace(",", "").replace("，", "")
        match = re.search(r"(?<!\d)([0-9]{1,6})\s*/\s*([0-9]{1,6})(?!\d)", text)
        if match:
            actual, maximum = map(int, match.groups())
            if actual <= maximum and maximum >= 30 and maximum % 3 == 0:
                distance = min(
                    (
                        abs(token.y - label.y) + abs(token.x - label.x)
                        for label in dx_labels
                    ),
                    default=1e9,
                )
                fractions.append((distance, -token.area, actual, maximum))
    if fractions and dx_labels:
        _, _, result.dx_score, result.max_dx_score = min(fractions)
    elif dx_labels:
        label = max(dx_labels, key=lambda token: token.score)
        hits = [
            token
            for token in tokens
            if integer(token.text) is not None
            and abs(token.x - label.x) < max(80, label.right - label.left)
            and 0 < token.y - label.y < max(80, (label.bottom - label.top) * 4)
        ]
        if hits:
            result.dx_score = integer(
                min(hits, key=lambda token: token.y - label.y).text
            )
    observed_table = (
        JudgementTable() if summary else parse_table(tokens, result.warnings)
    )
    table, merged = observed_table.cells, observed_table.merged
    result.partial_rows = table
    result.merged_perfect = merged
    if summary:
        values = []
        for aliases in HEADER_ALIASES:
            hits = [token for token in tokens if plain(token.text) in aliases]
            anchor = max(hits, key=lambda token: token.score) if hits else None
            counts = [
                token
                for token in tokens
                if anchor
                and integer(token.text) is not None
                and token.left > anchor.right
                and abs(token.y - anchor.y) < (anchor.bottom - anchor.top) * 0.7
            ]
            values.append(
                integer(min(counts, key=lambda token: token.x).text) if counts else None
            )
        if all(value is not None for value in values):
            result.aggregate = Judgements(*values)
    excluded_labels = set(
        HEADERS
        + DIFFICULTIES
        + NOTE_TYPES
        + (
            "JUDGEMENT",
            "FAST",
            "LATE",
            "GRADING",
            "FLUCTUATION",
            "DXSCORE",
            "MAXCOMBO",
            "CLEARS",
            "OFFSET",
            "DETAILS",
            "BRIEF",
            "NOMODS",
            "ACHIEVEMENT",
        )
    )
    title_texts = [
        plain(re.sub(r"\[(DX|SD)\]\s*$", "", token.text, flags=re.I))
        for token in tokens
        if len(plain(token.text)) >= 3 and plain(token.text) not in excluded_labels
    ]
    complete = {
        name: sum(value for value in cells if value is not None)
        for name, cells in table.items()
        if all(value is not None for value in cells[1 if merged else 0 :])
    }
    charts = []
    for music in musics:
        if int(music.id) >= 100000:
            continue
        title = plain(music.title)
        similarity = max(
            (SequenceMatcher(None, title, text).ratio() for text in title_texts),
            default=0,
        )
        for index, chart in enumerate(music.charts):
            if result.difficulty is not None and index != result.difficulty:
                continue
            notes = tuple(chart.notes)
            if len(notes) == 4:
                notes = (*notes[:3], 0, notes[3])
            matching = all(
                complete[name] == notes[NOTE_TYPES.index(name)] for name in complete
            )
            charts.append((similarity, matching, music, index, notes))
    exact = [chart for chart in charts if chart[0] == 1]
    by_notes = [chart for chart in charts if len(complete) >= 4 and chart[1]]
    candidates = exact or by_notes or [chart for chart in charts if chart[0] >= 0.78]
    if candidates:
        matching = [chart for chart in candidates if chart[1]]
        if matching:
            candidates = matching
        best = max(chart[0] for chart in candidates)
        candidates = [chart for chart in candidates if chart[0] == best]
        if len(candidates) == 1:
            _, _, music, index, expected_notes = candidates[0]
            result.music_id, result.title, result.difficulty = (
                str(music.id),
                music.title,
                index,
            )
        else:
            expected_notes = None
            result.warnings.append("歌曲或难度匹配存在多个候选，未指定歌曲 ID。")
    else:
        expected_notes = None
    for name in NOTE_TYPES:
        cells = table.get(name)
        if cells is None:
            if summary:
                continue
            if (
                expected_notes is not None
                and expected_notes[NOTE_TYPES.index(name)] == 0
            ):
                result.rows[name] = Judgements(None if merged else 0, 0, 0, 0, 0)
            continue
        required = range(1 if merged else 0, 5)
        missing = [index for index in required if cells[index] is None]
        if (
            expected_notes is not None
            and expected_notes[NOTE_TYPES.index(name)] == 0
            and all(cells[index] in (None, 0) for index in required)
        ):
            for index in required:
                cells[index] = 0
        if len(missing) == 1 and expected_notes is not None:
            recovered = expected_notes[NOTE_TYPES.index(name)] - sum(
                value for value in cells if value is not None
            )
            if recovered >= 0:
                cells[missing[0]] = recovered
                result.warnings.append(
                    f"{name} {HEADERS[missing[0]]} 未识别，由谱面物量与其余判定恢复为 {recovered}。"
                )
        if all(cells[index] is not None for index in required):
            result.rows[name] = Judgements(*cells)
    if not summary:
        result.analysis = analyse_judgements(
            result.rows,
            result.achievement,
            result.dx_score,
            expected_notes,
            result.max_dx_score,
            rounded=adx,
        )
    elif result.aggregate:
        total = result.aggregate
        calculated_dx = 3 * total.critical + 2 * total.perfect + total.great
        if result.dx_score is not None and calculated_dx != result.dx_score:
            result.warnings.append("DX 分与汇总判定不一致，请核对。")
        if expected_notes and sum(expected_notes) != total.total:
            result.warnings.append("汇总判定数量与谱面物量不一致，请核对。")
    return result


class ScoreRecognizer:
    """Lazily initialized local CPU OCR; callers serialize access in a worker thread."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.engine = None

    def recognize(self, path: Path, musics: list) -> RecognizedScore:
        """Read one local result image and calculate its score report.

        Args:
            path: Downloaded message image.
            musics: Public song catalog already loaded by maibot.

        Returns:
            Recognized score with validation and BREAK detail.

        Raises:
            ImportError: If OCR runtime dependencies have not been installed.
            ValueError: If the image is empty or too large.
            OSError: If the image or OCR models cannot be read.
        """
        from PIL import Image, ImageOps
        import numpy as np
        from rapidocr import RapidOCR

        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("图片超过 20 MB，请压缩后重试。")
        with Image.open(path) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError("图片分辨率过大，请缩小后重试。")
            source = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))[
                :, :, ::-1
            ].copy()
        if self.engine is None:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            self.engine = RapidOCR(
                params={
                    "Global.model_root_dir": str(self.model_dir),
                    "Global.log_level": "error",
                }
            )
        output = self.engine(source, use_det=True, use_cls=True, use_rec=True)
        if output.boxes is None or output.txts is None or output.scores is None:
            raise ValueError("未识别到文字，请发送清晰的完整结算图片。")
        tokens = [
            OCRToken.from_box(text, score, box)
            for box, text, score in zip(output.boxes, output.txts, output.scores)
        ]
        anchors = [
            token
            for token in tokens
            if any(
                plain(token.text).startswith(alias)
                for aliases in ROW_ALIASES.values()
                for alias in aliases
            )
        ]
        summary = any(plain(token.text) == "GRADING" for token in tokens)
        if summary:
            anchors = [
                token
                for token in tokens
                if plain(token.text) in ("CRITICAL", "PERFECT", "GREAT", "GOOD", "MISS")
            ]
        if len(anchors) >= 3:
            # Enlarge the judgement region so tiny zeroes and dark ADX cells
            # remain readable when the rest of a photo is much larger.
            import cv2

            spacing = median(
                right.y - left.y
                for left, right in zip(
                    sorted(anchors, key=lambda token: token.y),
                    sorted(anchors, key=lambda token: token.y)[1:],
                )
            )
            left = max(0, int(min(token.left for token in anchors) - spacing))
            top = max(0, int(min(token.top for token in anchors) - 2 * spacing))
            bottom = min(
                source.shape[0],
                int(max(token.bottom for token in anchors) + spacing * 0.6),
            )
            if not summary:
                positions = [
                    (NOTE_TYPES.index(name), token.y)
                    for token in anchors
                    for name, aliases in ROW_ALIASES.items()
                    if any(plain(token.text).startswith(alias) for alias in aliases)
                ]
                row_origin = median(y - index * spacing for index, y in positions)
                top = max(0, min(top, int(row_origin - 2 * spacing)))
                bottom = min(
                    source.shape[0], max(bottom, int(row_origin + 4.8 * spacing))
                )
            slope = median(token.slope for token in anchors)
            top = max(0, int(top + min(0, slope) * source.shape[1]))
            bottom = min(source.shape[0], int(bottom + max(0, slope) * source.shape[1]))
            # Keep all columns, including MISS; row labels anchor the left edge.
            right = source.shape[1]
            crop = source[top:bottom, left:right]
            scale = min(3.0, 1800 / crop.shape[1])
            enlarged = cv2.resize(
                crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )
            table_output = self.engine(
                enlarged, use_det=True, use_cls=True, use_rec=True
            )
            if (
                table_output.boxes is not None
                and table_output.txts is not None
                and table_output.scores is not None
            ):
                previous_tokens = tokens.copy()
                new_tokens = []
                for box, text, score in zip(
                    table_output.boxes, table_output.txts, table_output.scores
                ):
                    new = OCRToken.from_box(text, score, box, scale, (left, top))
                    # Enlarged detection can omit a leading digit. Keep the
                    # earlier, more confident full counter when its box contains
                    # the shorter observation; never reconstruct unseen digits.
                    if integer(new.text) is not None:
                        complete_counters = [
                            old
                            for old in previous_tokens
                            if integer(old.text) is not None
                            and len(old.text.strip()) > len(new.text.strip())
                            and new.text.strip() in old.text.strip()
                            and old.score >= max(0.8, new.score)
                            and old.left <= new.x <= old.right
                            and old.top <= new.y <= old.bottom
                        ]
                        if complete_counters:
                            new = max(complete_counters, key=lambda token: token.score)
                    new_tokens.append(new)
                # Keep first-pass cells when enlarging the table misses them.
                tokens = new_tokens + [
                    token
                    for token in previous_tokens
                    if not any(
                        abs(token.x - new.x) < max(8, (token.right - token.left) * 0.4)
                        and abs(token.y - new.y)
                        < max(5, (token.bottom - token.top) * 0.4)
                        for new in new_tokens
                    )
                ]
        if summary:
            anchors = [
                token
                for token in tokens
                if plain(token.text) in ("CRITICAL", "PERFECT", "GREAT", "GOOD", "MISS")
            ]
            ordered = sorted(anchors, key=lambda token: token.y)
            if len(ordered) == 5:
                spacing = median(b.y - a.y for a, b in zip(ordered, ordered[1:]))
                reference = [
                    token
                    for token in tokens
                    if integer(token.text) is not None
                    and token.left > ordered[0].right
                    and abs(token.y - ordered[0].y) < spacing * 0.4
                ]
                if reference:
                    x = min(reference, key=lambda token: token.x).x
                    for anchor in ordered:
                        # Read one counter per row; vertically merged "1 3"
                        # must not become thirteen in both GOOD and MISS rows.
                        left, right = (
                            max(0, int(x - spacing * 1.2)),
                            min(source.shape[1], int(x + spacing * 1.2)),
                        )
                        top, bottom = (
                            max(0, int(anchor.y - spacing * 0.4)),
                            min(source.shape[0], int(anchor.y + spacing * 0.4)),
                        )
                        cell_output = self.engine(
                            source[top:bottom, left:right],
                            use_det=False,
                            use_cls=False,
                            use_rec=True,
                        )
                        if (
                            cell_output.txts
                            and integer(cell_output.txts[0]) is not None
                        ):
                            tokens = [
                                token
                                for token in tokens
                                if not (
                                    left <= token.x <= right
                                    and top <= token.y <= bottom
                                )
                            ]
                            tokens.append(
                                OCRToken(
                                    str(cell_output.txts[0]),
                                    float(cell_output.scores[0]),
                                    left,
                                    top,
                                    right,
                                    bottom,
                                )
                            )
        else:
            observed = parse_ocr_tokens(tokens, musics)
            matched = next(
                (music for music in musics if str(music.id) == observed.music_id), None
            )
            retry_rows = set()
            if matched is not None and observed.difficulty is not None:
                notes = tuple(matched.charts[observed.difficulty].notes)
                if len(notes) == 4:
                    notes = (*notes[:3], 0, notes[3])
                retry_rows = {
                    name
                    for name, row in observed.rows.items()
                    if row.total != notes[NOTE_TYPES.index(name)]
                }
            table = parse_table(tokens, [])
            for (name, column), (x, y) in table.centers.items():
                if table.cells[name][column] is not None and name not in retry_rows:
                    continue
                left, right = (
                    max(0, int(x - table.column_step * 0.42)),
                    min(source.shape[1], int(x + table.column_step * 0.48)),
                )
                top, bottom = (
                    max(0, int(y - table.row_step * 0.38)),
                    min(source.shape[0], int(y + table.row_step * 0.38)),
                )
                cell_output = self.engine(
                    source[top:bottom, left:right],
                    use_det=False,
                    use_cls=False,
                    use_rec=True,
                )
                if (
                    cell_output.txts
                    and integer(cell_output.txts[0]) is not None
                    and cell_output.scores[0] >= 0.8
                ):
                    tokens = [
                        token
                        for token in tokens
                        if not (left <= token.x <= right and top <= token.y <= bottom)
                    ]
                    tokens.append(
                        OCRToken(
                            str(cell_output.txts[0]),
                            float(cell_output.scores[0]),
                            left,
                            top,
                            right,
                            bottom,
                        )
                    )
        result = parse_ocr_tokens(tokens, musics)
        if result.achievement is None or (
            result.analysis is not None and result.analysis.status == "inconsistent"
        ):
            # Large result digits may be split by font size, or 8 may become 0
            # in the full photo. Read their original pixels at local resolution.
            fragments = [
                token for token in tokens if re.search(r"\.[0-9]+[%％]", token.text)
            ]
            regions = []
            if fragments:
                focus = max(fragments, key=lambda token: token.area)
                height = focus.bottom - focus.top
                left = max(
                    0,
                    int(
                        focus.left - (3 if focus.text.startswith(".") else 0.1) * height
                    ),
                )
                right = min(source.shape[1], int(focus.right + height * 0.1))
                top = max(0, int(focus.top - height * 0.15))
                bottom = min(source.shape[0], int(focus.bottom + height * 0.15))
                regions.append((left, top, right, bottom, focus.y, height))
            for label in tokens:
                if plain(label.text) not in ("达成率", "ACHIEVEMENT"):
                    continue
                width = label.right - label.left
                height = label.bottom - label.top
                english = plain(label.text) == "ACHIEVEMENT"
                left = max(0, int(label.left - width * (0.5 if english else 3)))
                right = min(
                    source.shape[1], int(label.right + width * (1 if english else 8))
                )
                top = max(0, int(label.bottom))
                bottom = min(
                    source.shape[0],
                    int(
                        top
                        + max(
                            height * (4 if english else 6),
                            width * (0.5 if english else 2),
                        )
                    ),
                )
                regions.append(
                    (left, top, right, bottom, (top + bottom) / 2, bottom - top)
                )
            for left, top, right, bottom, center_y, height in regions:
                if right <= left or bottom <= top:
                    continue
                crop = source[top:bottom, left:right]
                for detect in (False, True):
                    output = self.engine(
                        crop, use_det=detect, use_cls=False, use_rec=True
                    )
                    recovered = []
                    if output.txts is not None and output.scores is not None:
                        for index, (text, confidence) in enumerate(
                            zip(output.txts, output.scores)
                        ):
                            if detect and output.boxes is not None:
                                token = OCRToken.from_box(
                                    text,
                                    confidence,
                                    output.boxes[index],
                                    offset=(left, top),
                                )
                                if abs(token.y - center_y) < height * 0.4:
                                    recovered.append(token)
                            elif confidence >= 0.98:
                                recovered.append(
                                    OCRToken(
                                        str(text),
                                        float(confidence),
                                        left,
                                        top,
                                        right,
                                        bottom,
                                    )
                                )
                    if not recovered:
                        continue
                    # Remove previous percentage observations, including the
                    # smaller personal best. Only this crop may supply a retry.
                    candidate = parse_ocr_tokens(
                        [
                            token
                            for token in tokens
                            if not re.search(r"[0-9]+[%％]", token.text)
                        ]
                        + recovered,
                        musics,
                    )
                    if candidate.achievement is not None and (
                        result.achievement is None
                        or candidate.analysis is not None
                        and candidate.analysis.status in ("unique", "ambiguous")
                    ):
                        result = candidate
                        break
                if (
                    result.achievement is not None
                    and result.analysis is not None
                    and result.analysis.status in ("unique", "ambiguous")
                ):
                    break
        return result
