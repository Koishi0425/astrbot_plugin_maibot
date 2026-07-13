import math
from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from .. import SIYUAN, TBFONT, diffs, get_botname, maimaidir, themepicdir
from .image import music_picture
from .maimaidx_model import Music


@dataclass(frozen=True)
class NoteLoss:
    label: str
    count: int
    great: float
    good: float
    miss: float


@dataclass(frozen=True)
class BreakLoss:
    perfect_1: float
    perfect_2: float
    great_1: float
    great_2: float
    great_3: float
    good: float
    miss: float


@dataclass(frozen=True)
class RankTolerance:
    rate: str
    margin: float
    great: int
    good: int
    miss: int


@dataclass(frozen=True)
class DxStarLine:
    star: int
    ratio: float
    score: int
    loss: int


@dataclass(frozen=True)
class ScorelineData:
    base_score: int
    total_notes: int
    max_dx_score: int
    note_losses: List[NoteLoss]
    break_count: int
    break_loss: BreakLoss
    tolerances: List[RankTolerance]
    dx_stars: List[DxStarLine]


def calculate_scoreline(music: Music, level_index: int) -> ScorelineData:
    chart = music.charts[level_index]
    notes = chart.notes
    tap = int(notes.tap)
    hold = int(notes.hold)
    slide = int(notes.slide)
    touch = int(getattr(notes, "touch", 0))
    brk = int(notes.brk)
    base_score = tap * 500 + hold * 1000 + slide * 1500 + touch * 500 + brk * 2500
    if base_score <= 0:
        raise ValueError("谱面音符数据无效")

    def base_loss(points: int) -> float:
        return points / base_score * 100

    note_losses = [
        NoteLoss("TAP", tap, base_loss(100), base_loss(250), base_loss(500)),
        NoteLoss("HOLD", hold, base_loss(200), base_loss(500), base_loss(1000)),
        NoteLoss("SLIDE", slide, base_loss(300), base_loss(750), base_loss(1500)),
    ]
    if touch:
        note_losses.append(NoteLoss("TOUCH", touch, base_loss(100), base_loss(250), base_loss(500)))

    def break_loss(base_points: int, bonus_points: int) -> float:
        base_part = base_loss(base_points)
        bonus_part = bonus_points / (brk * 100) if brk else 0
        return base_part + bonus_part

    break_result = BreakLoss(
        perfect_1=break_loss(0, 25),
        perfect_2=break_loss(0, 50),
        great_1=break_loss(500, 60),
        great_2=break_loss(1000, 60),
        great_3=break_loss(1250, 60),
        good=break_loss(1500, 70),
        miss=break_loss(2500, 100),
    )

    tap_great = base_loss(100)
    tap_good = base_loss(250)
    tap_miss = base_loss(500)

    def allowed(margin: float, loss: float) -> int:
        return math.floor((margin + 1e-10) / loss)

    tolerances = [
        RankTolerance(rate, margin, allowed(margin, tap_great), allowed(margin, tap_good), allowed(margin, tap_miss))
        for rate, margin in (("SSSp", 0.5), ("SSS", 1.0), ("SSp", 1.5), ("SS", 2.0), ("Sp", 3.0))
    ]

    total_notes = tap + hold + slide + touch + brk
    max_dx_score = total_notes * 3
    dx_stars = []
    for star, ratio in enumerate((0.85, 0.90, 0.93, 0.95, 0.97, 0.99), start=1):
        score = math.ceil(max_dx_score * ratio - 1e-10)
        dx_stars.append(DxStarLine(star, ratio, score, max_dx_score - score))

    return ScorelineData(
        base_score=base_score,
        total_notes=total_notes,
        max_dx_score=max_dx_score,
        note_losses=note_losses,
        break_count=brk,
        break_loss=break_result,
        tolerances=tolerances,
        dx_stars=dx_stars,
    )


def _font(path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _gradient(size: Tuple[int, int], top, bottom) -> Image.Image:
    strip = Image.new("RGB", (1, 2))
    strip.putpixel((0, 0), top)
    strip.putpixel((0, 1), bottom)
    return strip.resize(size)


def _background(width: int, height: int) -> Image.Image:
    path = themepicdir / "b50.png"
    if not path.exists():
        return _gradient((width, height), (184, 225, 255), (255, 205, 222)).convert("RGBA")
    source = Image.open(path).convert("RGBA")
    scaled_height = round(source.height * width / source.width)
    source = source.resize((width, scaled_height))
    top_height, bottom_height = 190, 125
    middle_height = height - top_height - bottom_height
    canvas = Image.new("RGBA", (width, height))
    canvas.paste(source.crop((0, 0, width, top_height)), (0, 0))
    middle = _gradient((width, middle_height), (173, 193, 251), (255, 199, 215)).convert("RGBA")
    middle_draw = ImageDraw.Draw(middle)
    for y in range(12, middle_height, 18):
        offset = 9 if (y // 18) % 2 else 0
        for x in range(8 + offset, width, 18):
            middle_draw.ellipse((x, y, x + 2, y + 2), fill=(255, 255, 255, 92))
    canvas.alpha_composite(middle, (0, top_height))
    canvas.paste(source.crop((0, scaled_height - bottom_height, width, scaled_height)), (0, height - bottom_height))
    return canvas


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _center_text(draw: ImageDraw.ImageDraw, box, text: str, font, fill) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (box[0] + box[2] - width) / 2 - bbox[0]
    y = (box[1] + box[3] - height) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _panel(draw: ImageDraw.ImageDraw, box, title: str, subtitle: str = "") -> None:
    draw.rounded_rectangle(box, radius=28, fill=(255, 255, 255, 224), outline=(255, 255, 255, 245), width=3)
    draw.rounded_rectangle((box[0] + 28, box[1] + 30, box[0] + 36, box[1] + 58), radius=4, fill="#26324f")
    draw.text((box[0] + 48, box[1] + 27), title, font=_font(SIYUAN, 25), fill="#26324f")
    if subtitle:
        draw.text((box[0] + 205, box[1] + 34), subtitle, font=_font(SIYUAN, 16), fill="#8b91a6")


def draw_scoreline(music: Music, level_index: int) -> Image.Image:
    data = calculate_scoreline(music, level_index)
    touch_extra = 68 if any(item.label == "TOUCH" for item in data.note_losses) else 0
    height = 1730 + touch_extra
    canvas = _background(1200, height)
    draw = ImageDraw.Draw(canvas)

    logo_path = themepicdir / "logo.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((210, 100))
        canvas.alpha_composite(logo, (58, 30))
    title_box = (955, 54, 1140, 99)
    draw.rounded_rectangle(title_box, radius=21, fill=(76, 137, 157, 210), outline="white", width=2)
    _center_text(draw, title_box, "分数线解析", _font(SIYUAN, 21), "white")

    # Song information
    info_box = (42, 150, 1158, 410)
    draw.rounded_rectangle(info_box, radius=30, fill=(255, 255, 255, 220), outline="white", width=3)
    cover_path = music_picture(music.id)
    if cover_path.exists():
        cover = Image.open(cover_path).convert("RGBA").resize((205, 205))
        mask = Image.new("L", cover.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 204, 204), radius=24, fill=255)
        canvas.paste(cover, (72, 178), mask)
    draw.text((315, 185), _fit(draw, music.title, _font(SIYUAN, 38), 560), font=_font(SIYUAN, 38), fill="#26324f")
    draw.text((315, 238), _fit(draw, music.basic_info.artist, _font(SIYUAN, 20), 560), font=_font(SIYUAN, 20), fill="#737d99")
    diff_colors = ["#71d94b", "#f4bb21", "#ff7180", "#a958df", "#ca9af1"]
    type_box = (315, 284, 395, 322)
    diff_text = f"{diffs[level_index]}  {music.ds[level_index]}"
    diff_font = _font(SIYUAN, 20)
    diff_width = int(draw.textlength(diff_text, font=diff_font)) + 42
    diff_box = (415, 284, 415 + diff_width, 322)
    draw.rounded_rectangle(type_box, radius=16, fill="#5f88dc" if music.type == "DX" else "#8e9aaa")
    draw.rounded_rectangle(diff_box, radius=16, fill=diff_colors[level_index])
    _center_text(draw, type_box, music.type, _font(TBFONT, 19), "white")
    _center_text(draw, diff_box, diff_text, diff_font, "white")
    genre_text = f"分区 · {music.basic_info.genre or '未知'}"
    version_text = str(music.basic_info.version or "未知版本")
    tag_font = _font(SIYUAN, 16)
    genre_width = min(190, int(draw.textlength(genre_text, font=tag_font)) + 34)
    genre_box = (620, 284, 620 + genre_width, 322)
    draw.rounded_rectangle(genre_box, radius=16, fill="#63b9c8")
    _center_text(draw, genre_box, _fit(draw, genre_text, tag_font, genre_width - 24), tag_font, "white")
    version_logo_path = maimaidir / f"{version_text}.png"
    version_area = (870, 195, 1120, 325)
    if version_logo_path.exists():
        version_logo = Image.open(version_logo_path).convert("RGBA")
        version_logo.thumbnail((version_area[2] - version_area[0], version_area[3] - version_area[1]))
        version_x = version_area[0] + (version_area[2] - version_area[0] - version_logo.width) // 2
        version_y = version_area[1] + (version_area[3] - version_area[1] - version_logo.height) // 2
        canvas.alpha_composite(version_logo, (version_x, version_y))
    else:
        fallback_box = (850, 284, 1115, 322)
        draw.rounded_rectangle(fallback_box, radius=16, fill="#7484b7")
        _center_text(draw, fallback_box, _fit(draw, version_text, tag_font, 235), tag_font, "white")
    meta = f"SONG ID  {music.id}     BPM  {music.basic_info.bpm}"
    draw.text((315, 348), meta, font=_font(SIYUAN, 17), fill="#596185")

    # Judgement loss section
    judgement_top = 440
    judgement_height = 560 + touch_extra
    judgement_box = (42, judgement_top, 1158, judgement_top + judgement_height)
    _panel(draw, judgement_box, "判定失分详情", "单次判定相对最大达成率的损失")
    header_y = judgement_top + 88
    draw.text((105, header_y), "音符类型", font=_font(SIYUAN, 17), fill="#848ba0")
    for x, text in ((520, "GREAT"), (755, "GOOD"), (985, "MISS")):
        _center_text(draw, (x - 80, header_y - 3, x + 80, header_y + 29), text, _font(TBFONT, 16), "#848ba0")
    row_top = header_y + 42
    for item in data.note_losses:
        draw.rounded_rectangle((76, row_top, 1124, row_top + 56), radius=18, fill=(255, 247, 255, 210))
        draw.rounded_rectangle((102, row_top + 12, 240, row_top + 44), radius=14, fill="white", outline="#e2dcec", width=2)
        _center_text(draw, (102, row_top + 12, 180, row_top + 44), item.label, _font(TBFONT, 16), "#32394e")
        _center_text(draw, (174, row_top + 12, 240, row_top + 44), str(item.count), _font(SIYUAN, 15), "#858ca0")
        for x, value, color in ((520, item.great, "#f15ea8"), (755, item.good, "#00ae82"), (985, item.miss, "#9aa2b4")):
            _center_text(draw, (x - 100, row_top + 7, x + 100, row_top + 49), f"-{value:.4f}%", _font(TBFONT, 19), color)
        row_top += 68

    break_top = row_top + 8
    draw.rounded_rectangle((76, break_top, 1124, break_top + 180), radius=20, fill=(255, 248, 246, 215))
    draw.rounded_rectangle((102, break_top + 16, 240, break_top + 48), radius=14, fill="white", outline="#e2dcec", width=2)
    _center_text(draw, (102, break_top + 16, 180, break_top + 48), "BREAK", _font(TBFONT, 16), "#32394e")
    _center_text(draw, (174, break_top + 16, 240, break_top + 48), str(data.break_count), _font(SIYUAN, 15), "#858ca0")
    break_values = [
        ("P1（50落）", data.break_loss.perfect_1, "#b85b00"),
        ("P2（100落）", data.break_loss.perfect_2, "#b85b00"),
        ("G1", data.break_loss.great_1, "#f15ea8"),
        ("G2", data.break_loss.great_2, "#f15ea8"),
        ("G3", data.break_loss.great_3, "#f15ea8"),
        ("GOOD", data.break_loss.good, "#00ae82"),
        ("MISS", data.break_loss.miss, "#9aa2b4"),
    ]
    cell_width = 140
    start_x = 92
    for index, (label, value, color) in enumerate(break_values):
        left = start_x + index * cell_width
        draw.rounded_rectangle((left, break_top + 70, left + 126, break_top + 154), radius=15, fill=(255, 255, 255, 205))
        _center_text(draw, (left, break_top + 79, left + 126, break_top + 107), label, _font(SIYUAN, 14), color)
        _center_text(draw, (left, break_top + 111, left + 126, break_top + 145), f"-{value:.4f}%", _font(TBFONT, 16), color)

    # Rating tolerance section
    rating_top = judgement_top + judgement_height + 28
    rating_box = (42, rating_top, 1158, rating_top + 310)
    _panel(draw, rating_box, "评级容错", "仅统计 TAP 失误 · 三列互斥 · 基准理论值 101%")
    card_top = rating_top + 88
    card_width = 196
    for index, item in enumerate(data.tolerances):
        left = 75 + index * 213
        draw.rounded_rectangle((left, card_top, left + card_width, card_top + 188), radius=20, fill=(255, 255, 255, 215), outline="#dedffa", width=2)
        rate_path = themepicdir / f"UI_TTR_Rank_{item.rate}.png"
        if rate_path.exists():
            rate = Image.open(rate_path).convert("RGBA")
            rate.thumbnail((120, 54))
            canvas.alpha_composite(rate, (left + (card_width - rate.width) // 2, card_top + 14))
        draw.rounded_rectangle((left + 32, card_top + 70, left + card_width - 32, card_top + 100), radius=14, fill="#f1edf0")
        _center_text(draw, (left + 32, card_top + 70, left + card_width - 32, card_top + 100), f"余量 +{item.margin:.2f}%", _font(SIYUAN, 14), "#777f94")
        draw.text((left + 22, card_top + 113), f"{item.great}  TAP Great", font=_font(SIYUAN, 15), fill="#f15ea8")
        draw.text((left + 22, card_top + 139), f"{item.good}  TAP Good", font=_font(SIYUAN, 15), fill="#00ae82")
        draw.text((left + 22, card_top + 165), f"{item.miss}  TAP Miss", font=_font(SIYUAN, 15), fill="#9099ab")

    # DX star section
    dx_top = rating_top + 338
    dx_box = (42, dx_top, 1158, dx_top + 264)
    _panel(draw, dx_box, "DX 星级", f"当前最大 {data.max_dx_score} DX 分")
    dx_card_top = dx_top + 88
    dx_width = 164
    star_colors = ["#6ee051", "#57d95c", "#ff9b35", "#ff9632", "#f4c52f", "#8068d9"]
    for index, item in enumerate(data.dx_stars):
        left = 75 + index * 174
        draw.rounded_rectangle((left, dx_card_top, left + dx_width, dx_card_top + 145), radius=18, fill=(255, 255, 255, 215), outline="#e2e2f3", width=2)
        stars = "★" * item.star
        _center_text(draw, (left + 5, dx_card_top + 10, left + dx_width - 5, dx_card_top + 42), stars, _font(SIYUAN, 18 if item.star < 6 else 16), star_colors[index])
        _center_text(draw, (left, dx_card_top + 43, left + dx_width, dx_card_top + 70), f"STAR {item.star}  ·  {item.ratio:.0%}", _font(TBFONT, 13), "#777f94")
        _center_text(draw, (left, dx_card_top + 72, left + dx_width, dx_card_top + 105), str(item.score), _font(TBFONT, 23), "#283047")
        draw.rounded_rectangle((left + 43, dx_card_top + 108, left + dx_width - 43, dx_card_top + 135), radius=12, fill="#fff0f1")
        _center_text(draw, (left + 43, dx_card_top + 108, left + dx_width - 43, dx_card_top + 135), f"-{item.loss}", _font(TBFONT, 13), "#f05b66")

    bot_name = str(get_botname() or "Bot").strip()
    bot_label = bot_name if bot_name.lower().endswith("bot") else f"{bot_name} Bot"
    credit = f"Designed By Glmg & Generated By {bot_label}"
    credit_font = _font(SIYUAN, 18)
    credit = _fit(draw, credit, credit_font, 920)
    credit_width = int(draw.textlength(credit, font=credit_font))
    credit_box = ((1200 - max(650, credit_width + 64)) / 2, height - 68, (1200 + max(650, credit_width + 64)) / 2, height - 28)
    draw.rounded_rectangle(credit_box, radius=20, fill=(255, 255, 255, 180), outline=(150, 158, 224, 185), width=2)
    _center_text(draw, credit_box, credit, credit_font, "#66709a")
    return canvas.convert("RGB")
