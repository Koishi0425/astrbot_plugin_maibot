"""Draw score cards with exact per-judgement achievement losses."""

from fractions import Fraction
from io import BytesIO
from math import ceil, cos, pi, sin
from pathlib import Path
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import SIYUAN, SHANGGUMONO, coverdir, maimaidir, themepicdir
from .maimaidx_score import NOTE_TYPES, NOTE_WEIGHTS, Judgements
from .maimaidx_score_recognition import DIFFICULTIES, RecognizedScore


def judgement_loss(rows: dict[str, Judgements], name: str, judgement: str) -> Fraction:
    """Calculate one note's achievement loss in percentage points.

    Args:
        rows: Complete observed table defining the chart's weighted maximum.
        name: Note type from NOTE_TYPES.
        judgement: CP, P, GREAT, GOOD, MISS or a named BREAK bin.

    Returns:
        Exact percentage points lost, including BREAK bonus loss.

    Raises:
        ValueError: The table is incomplete, empty or the judgement is unsupported.
    """
    if set(rows) != set(NOTE_TYPES):
        raise ValueError("A complete judgement table is required")
    maximum = sum(
        rows[key].total * weight for key, weight in zip(NOTE_TYPES, NOTE_WEIGHTS)
    )
    if not maximum or name not in NOTE_TYPES:
        raise ValueError("Invalid chart or note type")
    if judgement in ("CP", "P"):
        return Fraction(0)
    if name != "BREAK":
        ratios = {"GREAT": Fraction(1, 5), "GOOD": Fraction(1, 2), "MISS": Fraction(1)}
        if judgement not in ratios:
            raise ValueError("Invalid ordinary judgement")
        return (
            Fraction(NOTE_WEIGHTS[NOTE_TYPES.index(name)] * 100, maximum)
            * ratios[judgement]
        )
    count = rows["BREAK"].total
    if not count:
        return Fraction(0)
    if judgement in ("P50", "P100"):
        return Fraction(1 if judgement == "P50" else 2, 4 * count)
    losses = {
        "G2000": (500, 60),
        "G1500": (1000, 60),
        "G1250": (1250, 60),
        "GOOD": (1500, 70),
        "MISS": (2500, 100),
    }
    if judgement not in losses:
        raise ValueError("Invalid BREAK judgement")
    base, bonus = losses[judgement]
    return Fraction(base * 100, maximum) + Fraction(bonus, 100 * count)


class _ScoreCanvas:
    """Keep drawing state local to each request, with optional resource assets."""

    def __init__(self, height: int):
        """Prepare the font and decorative background for one result.

        Args:
            height: Result height, including any explanatory footnotes.

        Raises:
            OSError: No Chinese font is available in resources or system fonts.
        """
        candidates = (
            SIYUAN,
            SHANGGUMONO,
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        )
        self.font_path = next((path for path in candidates if path.is_file()), None)
        if self.font_path is None:
            raise OSError("Score image CJK font is unavailable")
        self.fonts = {}
        stops = (
            (0, (119, 220, 233)),
            (0.27, (160, 197, 253)),
            (0.43, (237, 171, 249)),
            (0.6, (250, 205, 207)),
            (0.88, (255, 227, 218)),
            (1, (255, 255, 255)),
        )
        positions = np.linspace(0, 1, height)
        rgb = np.stack(
            [
                np.interp(
                    positions, [p for p, _ in stops], [c[index] for _, c in stops]
                )
                for index in range(3)
            ],
            axis=1,
        )
        self.image = Image.fromarray(
            np.repeat(rgb.astype(np.uint8)[:, None, :], 1170, axis=1)
        ).convert("RGBA")
        self.draw = ImageDraw.Draw(self.image)
        decoration = Image.new("RGBA", self.image.size)
        draw = ImageDraw.Draw(decoration)
        for y in range(7, height, 13):
            for x in range(7, 1170, 13):
                draw.ellipse((x, y, x + 2, y + 2), fill=(255, 255, 255, 80))
        for x, y, scale in (
            (0, 30, 1),
            (930, 8, 1.2),
            (285, 125, 0.8),
            (1010, 165, 0.7),
        ):
            for cx, cy, r in ((30, 30, 37), (80, 15, 45), (124, 34, 36)):
                cx, cy, r = x + cx * scale, y + cy * scale, r * scale
                draw.ellipse(
                    (cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 140)
                )
        for x, y in (
            (27, 320),
            (1120, 700),
            (24, 1140),
            (1115, 360),
            (243, 80),
            (830, 37),
        ):
            draw.polygon(
                ((x, y - 9), (x + 9, y), (x, y + 9), (x - 9, y)),
                fill=(255, 249, 191, 210),
            )
        center = (585, height + 115)
        for radius, color in zip(
            range(295, 224, -14),
            (
                (252, 152, 163),
                (255, 213, 123),
                (246, 249, 150),
                (173, 232, 173),
                (148, 218, 239),
                (192, 185, 247),
            ),
        ):
            draw.arc(
                (
                    center[0] - radius,
                    center[1] - radius,
                    center[0] + radius,
                    center[1] + radius,
                ),
                180,
                360,
                fill=color + (190,),
                width=14,
            )
        self.image.alpha_composite(decoration)

    def text(self, xy, value, size=24, color="#25252e", *, anchor="lm", width=None):
        """Draw fitted Unicode text using a per-card font cache.

        Args:
            xy: Text anchor coordinates.
            value: Text to draw.
            size: Preferred font size.
            color: Pillow color.
            anchor: Pillow text anchor.
            width: Optional maximum width; long text shrinks or ellipsizes.
        """
        value = str(value)
        while True:
            if size not in self.fonts:
                self.fonts[size] = ImageFont.truetype(str(self.font_path), size)
            font = self.fonts[size]
            if width is None or font.getlength(value) <= width or size <= 13:
                break
            size -= 1
        if width is not None:
            while value and font.getlength(value) > width:
                value = value[:-2] + "…"
        self.draw.text(xy, value, font=font, fill=color, anchor=anchor)

    def card(self, box, fill=(255, 255, 255, 205), radius=30):
        """Composite a translucent rounded panel.

        Args:
            box: Panel rectangle.
            fill: RGBA background color.
            radius: Corner radius.
        """
        panel = Image.new("RGBA", self.image.size)
        ImageDraw.Draw(panel).rounded_rectangle(
            box, radius, fill, outline="white", width=2
        )
        self.image.alpha_composite(panel)

    def asset(self, path: Path, box, *, rounded=False) -> bool:
        """Place an optional local resource without failing the whole score card.

        Args:
            path: Logo, cover or version image.
            box: Target rectangle.
            rounded: Whether to crop and mask the cover's corners.

        Returns:
            Whether the asset could be drawn.
        """
        try:
            with Image.open(path) as source:
                dimensions = (box[2] - box[0], box[3] - box[1])
                source = (
                    ImageOps.fit(source.convert("RGBA"), dimensions)
                    if rounded
                    else ImageOps.contain(source.convert("RGBA"), dimensions)
                )
                if rounded:
                    mask = Image.new("L", dimensions)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        (0, 0, dimensions[0], dimensions[1]), 22, fill=255
                    )
                    source.putalpha(mask)
                self.image.alpha_composite(source, (box[0], box[1]))
            return True
        except (OSError, ValueError):
            return False

    def star(self, x, y, radius, color):
        """Draw a five-point DX star.

        Args:
            x: Horizontal center.
            y: Vertical center.
            radius: Outer radius.
            color: Star fill.
        """
        points = [
            (
                x
                + (radius if i % 2 == 0 else radius * 0.43) * cos(-pi / 2 + i * pi / 5),
                y
                + (radius if i % 2 == 0 else radius * 0.43) * sin(-pi / 2 + i * pi / 5),
            )
            for i in range(10)
        ]
        self.draw.polygon(points, fill=color, outline="white", width=1)

    def cell(self, x, y, count, unit=Fraction(0)):
        """Draw count, total deduction and per-note deduction.

        Args:
            x: Cell horizontal center.
            y: Cell vertical center.
            count: Observed count or None.
            unit: Exact per-note loss in percentage points.
        """
        self.text(
            (x, y - 19 if count and unit else y),
            "?" if count is None else count,
            29,
            "#c2c5d0" if count == 0 else "#25252e",
            anchor="mm",
        )
        if count and unit:
            self.draw.rounded_rectangle(
                (x - 49, y + 2, x + 49, y + 28), 13, fill="#ffe3e5"
            )
            self.text(
                (x, y + 15), f"-{float(unit * count):.4f}%", 15, "#ff4662", anchor="mm"
            )
            self.draw.line((x - 40, y + 35, x + 40, y + 35), fill="#e8c7ca", width=1)
            self.text((x, y + 48), f"-{float(unit):.4f}%", 13, "#b97880", anchor="mm")

    def break_cell(self, x, y, label, counts, unit):
        """Draw a named BREAK bin, retaining candidate ranges.

        Args:
            x: Cell center.
            y: Row center.
            label: Explicit 50/100 loss or GREAT base-score label.
            counts: Values across all compatible candidates.
            unit: Exact per-note percentage loss.
        """
        low, high = min(counts), max(counts)
        value = str(low) if low == high else f"{low}～{high}"
        self.draw.rounded_rectangle(
            (x - 79, y - 13, x - 24, y + 13),
            12,
            fill="#fff0c4" if "落" in label else "#ffe0e8",
        )
        self.text(
            (x - 51, y),
            label,
            14,
            "#ae8b21" if "落" in label else "#ed5583",
            anchor="mm",
        )
        self.text(
            (x - 4, y),
            value,
            21,
            "#c2c5d0" if high == 0 else "#25252e",
            anchor="mm",
            width=45,
        )
        if high:
            self.draw.rounded_rectangle(
                (x + 19, y - 17, x + 90, y + 5), 10, fill="#ffe2d8"
            )
            self.text(
                (x + 55, y - 6),
                f"-{float(unit * low):.4f}%" if low == high else "范围见下",
                12,
                "#fa5570",
                anchor="mm",
                width=70,
            )
            if low != high:
                self.text(
                    (x + 55, y + 16),
                    f"{float(unit * low):.4f}～{float(unit * high):.4f}",
                    10,
                    "#ba7b76",
                    anchor="mm",
                    width=85,
                )
            else:
                self.text(
                    (x + 55, y + 17), f"-{float(unit):.4f}%", 11, "#ba7b76", anchor="mm"
                )


def render_score_image(score: RecognizedScore, music=None) -> bytes:
    """Render a pastel result image following the supplied score-card template.

    Args:
        score: Complete, consistent score with compatible BREAK candidates.
        music: Matched public music entry, including cover and chart metadata.

    Returns:
        PNG bytes, held in memory so concurrent requests never share a file.

    Raises:
        ValueError: The score lacks a complete validated judgement table.
        OSError: No usable Chinese font is installed.
    """
    if (
        not score.analysis
        or score.analysis.status not in ("unique", "ambiguous")
        or set(score.rows) != set(NOTE_TYPES)
    ):
        raise ValueError("Complete consistent score data is required")
    notes = score.rows
    details = score.analysis.candidates
    footnotes = []
    if any(row.critical is None for row in notes.values()):
        footnotes.append("PERFECT 含 CP；普通键型无法拆分，BREAK 行显示可计算的细分。")
    if len(details) > 1:
        footnotes.append(
            "细分栏以区间展示所有符合计分规则的数量；失分范围单位为百分比。"
        )
    footnotes.extend(
        warning for warning in dict.fromkeys(score.warnings) if warning not in footnotes
    )
    card = _ScoreCanvas(1590 + len(footnotes) * 52)
    card.asset(themepicdir / "logo.png", (65, 32, 269, 129))
    card.draw.rounded_rectangle((1040, 58, 1125, 107), 24, fill=(97, 127, 147, 160))
    card.text((1082, 82), "识分", 22, "white", anchor="mm")
    card.card((36, 154, 1134, 433))
    cover_id = int(score.music_id or 0)
    covers = (coverdir / f"{cover_id}.png", coverdir / f"{cover_id % 10000}.png")
    cover = next((path for path in covers if cover_id and path.is_file()), None)
    card.card((68, 188, 282, 403), fill=(247, 247, 255, 240), radius=23)
    if not cover or not card.asset(cover, (72, 192, 278, 399), rounded=True):
        card.text((175, 292), "封面暂无", 22, "#a4a3ba", anchor="mm")
    song_type = getattr(music, "type", "")
    card.draw.rounded_rectangle(
        (313, 194, 421, 229), 16, fill="white", outline="#349bed", width=2
    )
    card.text(
        (367, 212),
        "DX" if song_type == "DX" else ("STANDARD" if song_type == "SD" else "maimai"),
        18,
        "#349bed",
        anchor="mm",
        width=98,
    )
    basic = getattr(music, "basic_info", None)
    version = getattr(basic, "version", "")
    # Versions are catalog labels, never arbitrary file paths supplied by a user.
    if version and Path(version).name == version:
        card.asset(maimaidir / f"{version}.png", (667, 188, 787, 238))
    card.text((313, 274), score.title or "未知歌曲", 38, width=475)
    card.text((313, 307), getattr(basic, "artist", "—"), 17, "#737480", width=470)
    card.draw.line((313, 334, 781, 334), fill="#d7d0e4", width=2)
    card.draw.line((813, 253, 813, 341), fill="#d7d0e4", width=2)
    card.text((1101, 272), "ACHIEVEMENT", 16, "#737480", anchor="rm")
    card.text((1102, 316), (score.achievement or "?") + "%", 45, anchor="rm", width=272)
    index = score.difficulty
    levels = getattr(music, "level", [])
    constants = getattr(music, "ds", [])
    level = levels[index] if index is not None and index < len(levels) else "?"
    constant = constants[index] if index is not None and index < len(constants) else "?"
    colors = ("#72bd46", "#dfad2c", "#ef597c", "#ab50e4", "#9366ce")
    difficulty = DIFFICULTIES[index] if index is not None else "?"
    for x, label, value, width in (
        (313, "DIFFICULTY", f"{difficulty} {level}", 145),
        (472, "CONSTANT", constant, 96),
        (591, "SONG ID", score.music_id or "?", 95),
        (709, "BPM", getattr(basic, "bpm", "?"), 72),
    ):
        card.text((x, 361), label, 14, "#767280")
        card.text(
            (x, 394),
            value,
            23,
            colors[index] if label == "DIFFICULTY" and index is not None else "#25252e",
            width=width,
        )
    card.card((36, 458, 1134, 645), fill=(255, 244, 252, 215))
    card.text((69, 491), "DX SCORE", 16, "#767280")
    actual = score.dx_score if score.dx_score is not None else score.analysis.dx_score
    maximum = score.max_dx_score or score.analysis.max_dx_score
    ratio = Fraction(actual, maximum) if actual is not None and maximum else None
    stars = (
        sum(ratio >= Fraction(threshold, 100) for threshold in (85, 90, 93, 95, 97))
        if ratio is not None
        else 0
    )
    if stars:
        card.star(100, 534, 11, "#82d350")
        card.text((119, 534), stars, 18, "#82b540")
    card.text((147, 535), f"{actual if actual is not None else '?'} / {maximum}", 33)
    card.text(
        (799, 536),
        f"{float(ratio * 100):.2f}%" if ratio is not None else "?",
        23,
        "#687180",
        anchor="rm",
    )
    card.draw.line((830, 460, 830, 643), fill="#eee0eb", width=2)
    card.text((980, 535), "总物量", 20, "#85828d", anchor="mm")
    card.text((980, 580), sum(row.total for row in notes.values()), 29, anchor="mm")
    card.draw.rounded_rectangle((69, 610, 799, 624), 7, fill="#e7cfe4")
    if ratio is not None:
        # Expand the useful DX-star region while keeping all score values exact.
        progress = min(1, max(0, (float(ratio) - 0.8) / 0.2))
        if progress:
            card.draw.rounded_rectangle(
                (69, 610, 69 + 730 * progress, 624), 7, fill="#8bd16c"
            )
        for count, threshold, color in (
            (1, 85, "#83cf58"),
            (2, 90, "#83cf58"),
            (3, 93, "#ffae55"),
            (4, 95, "#ebcc50"),
            (5, 97, "#7168c1"),
        ):
            x = 69 + 730 * (threshold - 80) / 20
            card.star(x, 576, 9, color)
            card.text((x + 13, 576), count, 12, color)
            delta = actual - ceil(Fraction(maximum * threshold, 100))
            card.text((x, 595), f"{delta:+d}", 13, anchor="mm")
            card.draw.line((x, 610, x, 624), fill="#b8a2b9", width=2)
    card.card((36, 670, 1134, 1467), fill=(255, 249, 250, 217))
    card.draw.rounded_rectangle((65, 702, 70, 724), 2, fill="#9d65e6")
    card.text((82, 715), "判定分布 & 失分", 27)
    card.text((332, 715), "每格：数量 / 合计扣分 / 单个扣分", 17, "#827b83")
    merged = any(row.critical is None for row in notes.values())
    centers = (116, 276, 459, 643, 827, 1010)
    for x, label, color in zip(
        centers,
        (
            "类型",
            "CRITICAL",
            "PERFECT（含 CP）" if merged else "PERFECT",
            "GREAT",
            "GOOD",
            "MISS",
        ),
        ("#85828d", "#e6a100", "#ba9900", "#ef518e", "#0cb294", "#93959f"),
    ):
        card.text((x, 763), label, 16, color, anchor="mm", width=178)
    for position, name in enumerate(NOTE_TYPES):
        top = 789 + position * 113
        height = 195 if name == "BREAK" else 103
        card.card(
            (65, top, 1105, top + height),
            fill=(255, 246, 223, 235) if name == "BREAK" else (255, 255, 255, 200),
            radius=19,
        )
        middle = top + height / 2
        card.draw.rounded_rectangle(
            (centers[0] - 39, middle - 16, centers[0] + 39, middle + 16),
            14,
            fill="white",
        )
        card.text((centers[0], middle), name, 17, anchor="mm")
        row = notes[name]
        if name != "BREAK":
            for x, value, judgement in zip(
                centers[1:],
                (row.critical, row.perfect, row.great, row.good, row.miss),
                ("CP", "P", "GREAT", "GOOD", "MISS"),
            ):
                card.cell(x, middle, value, judgement_loss(notes, name, judgement))
        else:
            cp = row.critical
            if cp is None:
                cp_values = [
                    row.perfect - candidate.perfect_50 - candidate.perfect_100
                    for candidate in details
                ]
                cp = (
                    str(min(cp_values))
                    if min(cp_values) == max(cp_values)
                    else f"{min(cp_values)}～{max(cp_values)}"
                )
            card.cell(centers[1], middle, cp)
            for label, attribute, judgement, y in (
                ("50落", "perfect_50", "P50", middle - 31),
                ("100落", "perfect_100", "P100", middle + 31),
            ):
                card.break_cell(
                    centers[2],
                    y,
                    label,
                    [getattr(candidate, attribute) for candidate in details],
                    judgement_loss(notes, name, judgement),
                )
            for label, attribute, judgement, y in (
                ("2000", "great_2000", "G2000", middle - 57),
                ("1500", "great_1500", "G1500", middle),
                ("1250", "great_1250", "G1250", middle + 57),
            ):
                card.break_cell(
                    centers[3],
                    y,
                    label,
                    [getattr(candidate, attribute) for candidate in details],
                    judgement_loss(notes, name, judgement),
                )
            card.cell(centers[4], middle, row.good, judgement_loss(notes, name, "GOOD"))
            card.cell(centers[5], middle, row.miss, judgement_loss(notes, name, "MISS"))
    for i, note in enumerate(footnotes):
        card.text((69, 1498 + i * 52), note, 18, "#887a86", width=1030)
    footer = card.image.height - 43
    card.draw.rounded_rectangle(
        (372, footer - 20, 798, footer + 20), 20, fill=(97, 139, 150, 170)
    )
    card.text((585, footer), "mai识分  ·  AstrBot maibot", 18, "white", anchor="mm")
    output = BytesIO()
    card.image.convert("RGB").save(output, "PNG")
    return output.getvalue()
