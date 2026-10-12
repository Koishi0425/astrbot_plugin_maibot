"""Offline regression tests for maimai score reconstruction and message inputs."""

import ast
import asyncio
import json
from fractions import Fraction
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_plugin_maibot.libraries.maimaidx_score import (
    BreakDetails,
    Judgements,
    NOTE_TYPES,
    achievement_interval,
    analyse_judgements,
    calculate_achievement,
)
from astrbot_plugin_maibot.libraries.maimaidx_score_recognition import (
    OCRToken,
    parse_ocr_tokens,
)


def token(text, x, y, width=42, height=18):
    """Build a synthetic OCR observation.

    Args:
        text: Observed label or number.
        x: Horizontal center.
        y: Vertical center.
        width: Bounding-box width.
        height: Bounding-box height.

    Returns:
        An OCR token with full confidence.
    """
    return OCRToken(
        text, 0.99, x - width / 2, y - height / 2, x + width / 2, y + height / 2
    )


def table_tokens(rows, achievement, title="Test song", *, merged=False):
    """Build a complete judgement table and its identifying fields.

    Args:
        rows: Note row counts.
        achievement: Displayed percentage.
        title: Song title.
        merged: Whether PERFECT includes CRITICAL PERFECT.

    Returns:
        Synthetic tokens in a typical ADX table layout.
    """
    headers = ("CRIT.", "PERF.", "GREAT", "GOOD", "MISS")
    tokens = [
        token(title, 200, 30, width=180),
        token("Master", 500, 30),
        token(achievement + "%", 300, 75, width=200, height=50),
    ]
    for column, label in enumerate(headers):
        if column or not merged:
            tokens.append(token(label, 300 + column * 90, 130))
    for index, name in enumerate(NOTE_TYPES):
        y = 175 + 42 * index
        tokens.append(token(name, 160, y))
        for column, value in enumerate(vars(rows[name]).values()):
            if value is not None:
                tokens.append(token(str(value), 300 + column * 90, y))
    return tokens


def music(rows, title="Test song", music_id="12345"):
    """Build a minimal public catalog entry.

    Args:
        rows: Rows defining the chart note totals.
        title: Song title.
        music_id: Catalog ID.

    Returns:
        Catalog object compatible with the maibot Music model.
    """
    notes = tuple(rows[name].total for name in NOTE_TYPES)
    return SimpleNamespace(
        id=music_id,
        title=title,
        type="DX",
        charts=[SimpleNamespace(notes=notes) for _ in range(4)],
    )


class ExactScoringTests(unittest.TestCase):
    def setUp(self):
        self.rows = {name: Judgements(0, 0, 0, 0, 0) for name in NOTE_TYPES}
        self.rows["TAP"] = Judgements(100, 0, 0, 0, 0)

    def test_all_break_bins_match_base_and_bonus_rules(self):
        cases = (
            (Judgements(1, 0, 0, 0, 0), BreakDetails(), 2500, 100),
            (Judgements(0, 1, 0, 0, 0), BreakDetails(perfect_50=1), 2500, 75),
            (Judgements(0, 1, 0, 0, 0), BreakDetails(perfect_100=1), 2500, 50),
            (Judgements(0, 0, 1, 0, 0), BreakDetails(great_2000=1), 2000, 40),
            (Judgements(0, 0, 1, 0, 0), BreakDetails(great_1500=1), 1500, 40),
            (Judgements(0, 0, 1, 0, 0), BreakDetails(great_1250=1), 1250, 40),
            (Judgements(0, 0, 0, 1, 0), BreakDetails(), 1000, 30),
            (Judgements(0, 0, 0, 0, 1), BreakDetails(), 0, 0),
        )
        for row, details, base, bonus in cases:
            with self.subTest(row=row, details=details):
                self.rows["BREAK"] = row
                expected = Fraction(100 * (50_000 + base), 52_500) + Fraction(
                    bonus, 100
                )
                self.assertEqual(calculate_achievement(self.rows, details), expected)

    def test_normal_great_good_and_miss_have_note_weighted_losses(self):
        self.rows = {name: Judgements(0, 0, 1, 1, 1) for name in NOTE_TYPES[:-1]}
        self.rows["BREAK"] = Judgements(0, 0, 0, 0, 0)
        self.assertEqual(
            calculate_achievement(self.rows, BreakDetails()), Fraction(130, 3)
        )

    def test_no_break_chart_caps_at_100_percent(self):
        self.rows["TAP"] = Judgements(50, 50, 0, 0, 0)
        self.assertEqual(calculate_achievement(self.rows, BreakDetails()), 100)
        self.assertEqual(
            analyse_judgements(self.rows, "100.0000", 250).status, "unique"
        )

    def test_all_critical_with_break_caps_at_101_percent(self):
        self.rows["BREAK"] = Judgements(10, 0, 0, 0, 0)
        self.assertEqual(calculate_achievement(self.rows, BreakDetails()), 101)

    def test_exact_display_boundary_does_not_use_float_rounding(self):
        self.rows["BREAK"] = Judgements(7, 3, 0, 0, 0)
        self.assertEqual(
            calculate_achievement(self.rows, BreakDetails(1, 2)), Fraction(807, 8)
        )
        result = analyse_judgements(self.rows, "100.875000", 327)
        self.assertEqual(result.candidates, [BreakDetails(1, 2)])
        self.assertEqual(
            achievement_interval("100.875000"),
            (Fraction(807, 8), Fraction(100875001, 1000000)),
        )

    def test_three_great_can_have_multiple_exact_solutions(self):
        self.rows["BREAK"] = Judgements(0, 0, 3, 0, 0)
        first = BreakDetails(great_2000=1, great_1250=2)
        second = BreakDetails(great_1500=3)
        value = calculate_achievement(self.rows, first)
        self.assertEqual(value, calculate_achievement(self.rows, second))
        scaled = value.numerator * 1_000_000 // value.denominator
        display = f"{scaled // 1_000_000}.{scaled % 1_000_000:06d}"
        result = analyse_judgements(self.rows, display)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(set(result.candidates), {first, second})

    def test_invalid_counts_and_wrong_break_totals_are_rejected(self):
        self.rows["BREAK"] = Judgements(0, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            calculate_achievement(self.rows, BreakDetails())
        with self.assertRaises(ValueError):
            calculate_achievement(self.rows, BreakDetails(-1, 2))
        self.rows["TAP"] = Judgements(-1, 0, 0, 0, 0)
        self.assertEqual(
            analyse_judgements(self.rows, "100.0000").status, "inconsistent"
        )

    def test_dx_and_note_total_mismatches_are_rejected(self):
        self.assertEqual(
            analyse_judgements(self.rows, "100.0000", 299).status, "inconsistent"
        )
        self.assertEqual(
            analyse_judgements(
                self.rows, "100.0000", expected_notes=(99, 0, 0, 0, 0)
            ).status,
            "inconsistent",
        )
        self.assertEqual(
            analyse_judgements(self.rows, "100.0000", max_dx_score=303).status,
            "inconsistent",
        )

    def test_missing_achievement_does_not_guess_break_bins(self):
        self.rows["BREAK"] = Judgements(2, 2, 1, 0, 0)
        self.assertEqual(analyse_judgements(self.rows, None).status, "insufficient")

    def test_invalid_or_impossible_achievement_is_rejected(self):
        for display in ("101.0001", "garbled", "99.1234"):
            self.assertEqual(
                analyse_judgements(self.rows, display).status, "inconsistent"
            )

    def test_wechat_sample_unique_break_solution(self):
        rows = {
            "TAP": Judgements(491, 328, 41, 4, 2),
            "HOLD": Judgements(63, 47, 2, 0, 1),
            "SLIDE": Judgements(90, 0, 0, 0, 0),
            "TOUCH": Judgements(53, 0, 0, 0, 0),
            "BREAK": Judgements(78, 8, 2, 0, 1),
        }
        result = analyse_judgements(rows, "99.7643", 3136, (866, 113, 90, 53, 89), 3633)
        self.assertEqual(result.status, "unique")
        self.assertEqual(result.candidates, [BreakDetails(6, 2, 2, 0, 0)])

    def test_cabinet_sample_unique_break_solution(self):
        rows = {
            "TAP": Judgements(453, 226, 17, 0, 1),
            "HOLD": Judgements(28, 12, 0, 0, 0),
            "SLIDE": Judgements(93, 0, 0, 1, 0),
            "TOUCH": Judgements(19, 0, 0, 0, 0),
            "BREAK": Judgements(18, 19, 1, 0, 0),
        }
        self.assertEqual(
            analyse_judgements(rows, "100.2098", 2365).candidates,
            [BreakDetails(15, 4, 0, 1, 0)],
        )

    def test_adx_six_decimal_sample_unique_break_solution(self):
        rows = {
            "TAP": Judgements(337, 158, 17, 0, 0),
            "HOLD": Judgements(35, 21, 0, 0, 0),
            "SLIDE": Judgements(74, 0, 1, 0, 0),
            "TOUCH": Judgements(60, 0, 0, 0, 0),
            "BREAK": Judgements(62, 16, 0, 0, 0),
        }
        result = analyse_judgements(rows, "100.640789", 2112, rounded=True)
        self.assertEqual(result.status, "unique")
        self.assertEqual(result.candidates, [BreakDetails(16, 0)])
        self.assertEqual(
            analyse_judgements(rows, "100.640789", 2112).status, "inconsistent"
        )


class TableRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        self.rows["BREAK"] = Judgements(7, 3, 0, 0, 0)
        self.tokens = table_tokens(self.rows, "100.875000")
        self.catalog = [music(self.rows)]

    def test_table_song_id_and_unique_solution(self):
        result = parse_ocr_tokens(self.tokens, self.catalog)
        self.assertEqual(result.music_id, "12345")
        self.assertEqual(result.rows, self.rows)
        self.assertEqual(result.analysis.candidates, [BreakDetails(1, 2)])

    def test_chinese_labels_and_two_line_critical_perfect_header(self):
        aliases = {
            "TAP": "点击",
            "HOLD": "长按",
            "SLIDE": "滑动",
            "TOUCH": "触摸",
            "BREAK": "绝赞",
        }
        tokens = [token(aliases.get(t.text, t.text), t.x, t.y) for t in self.tokens]
        tokens.extend([token("CRITICAL", 300, 119), token("PERFECT", 300, 132)])
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertEqual(result.rows, self.rows)

    def test_four_column_perfect_counts_are_not_all_small_p(self):
        rows = {name: Judgements(None, 10, 0, 0, 0) for name in NOTE_TYPES}
        tokens = table_tokens(rows, "100.875000", merged=True)
        tokens.extend([token("DX SCORE", 450, 480), token("147/150", 450, 510)])
        result = parse_ocr_tokens(tokens, [music(rows)])
        self.assertTrue(all(row.critical is None for row in result.rows.values()))
        self.assertEqual(result.analysis.candidates, [BreakDetails(1, 2)])

    def test_one_missing_cell_is_recovered_from_chart_not_zero(self):
        tokens = [t for t in self.tokens if not (t.x == 390 and t.y == 343)]
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertEqual(result.rows["BREAK"].perfect, 3)
        self.assertTrue(any("恢复为 3" in text for text in result.warnings))

    def test_two_missing_cells_remain_incomplete(self):
        tokens = [t for t in self.tokens if not (t.x in (390, 480) and t.y == 343)]
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertNotIn("BREAK", result.rows)
        self.assertEqual(result.analysis.status, "insufficient")

    def test_missing_break_label_is_recovered_by_geometry(self):
        result = parse_ocr_tokens(
            [t for t in self.tokens if t.text != "BREAK"], self.catalog
        )
        self.assertEqual(result.rows, self.rows)
        self.assertTrue(any("BREAK 标签" in text for text in result.warnings))

    def test_missing_critical_header_still_detects_five_columns(self):
        result = parse_ocr_tokens(
            [t for t in self.tokens if t.text != "CRIT."], self.catalog
        )
        self.assertEqual(result.rows, self.rows)

    def test_difficulty_is_not_assumed_when_absent(self):
        result = parse_ocr_tokens(
            [t for t in self.tokens if t.text != "Master"], self.catalog
        )
        self.assertIsNone(result.music_id)
        self.assertIsNone(result.difficulty)

    def test_observed_counts_are_not_rewritten_to_match_catalog(self):
        tokens = [
            token("8", t.x, t.y) if t.x == 300 and t.y == 343 else t
            for t in self.tokens
        ]
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertEqual(result.rows["BREAK"].critical, 8)
        self.assertEqual(result.analysis.status, "inconsistent")

    def test_achievement_is_largest_display_and_keeps_precision(self):
        tokens = self.tokens + [token("99.0000% +1.8750%", 300, 105, width=120)]
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertEqual(result.achievement, "100.875000")

    def test_empty_image_has_no_fabricated_rows(self):
        result = parse_ocr_tokens([], self.catalog)
        self.assertEqual(result.rows, {})
        self.assertEqual(result.analysis.status, "insufficient")

    def test_adx_summary_reports_observed_totals_without_spreading_them(self):
        tokens = [
            token("Test song[DX]", 200, 30),
            token("Master", 500, 30),
            token("GRADING", 200, 100),
            token("FLUCTUATION", 500, 100),
            token("100.875000%", 300, 70, width=200, height=50),
        ]
        for index, (label, value) in enumerate(
            zip(("Critical", "Perfect", "Great", "Good", "Miss"), (47, 3, 0, 0, 0))
        ):
            tokens.extend(
                [
                    token(label, 150, 150 + index * 40),
                    token(str(value), 300, 150 + index * 40),
                ]
            )
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertEqual(result.aggregate, Judgements(47, 3, 0, 0, 0))
        self.assertEqual(result.rows, {})
        self.assertIn("Details", result.format())


class ScoreCommandTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse(
            Path(__file__).with_name("main.py").read_text(encoding="utf-8")
        )
        plugin = next(
            node
            for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "MaimaiDXPlugin"
        )
        handler = next(
            node
            for node in plugin.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "mai_score_recognition"
        )
        cls.pattern = handler.decorator_list[0].args[0].value
        handler.decorator_list = []
        namespace = {
            "__package__": "astrbot_plugin_maibot",
            "AstrMessageEvent": object,
            "asyncio": asyncio,
            "Path": Path,
            "mai": SimpleNamespace(total_list=[]),
            "logger": Mock(),
        }
        exec(
            compile(ast.Module(body=[handler], type_ignores=[]), "main.py", "exec"),
            namespace,
        )
        cls.handler = namespace["mai_score_recognition"]

    def setUp(self):
        self.recognize = Mock(return_value=SimpleNamespace(format=lambda: "recognized"))
        self.plugin = SimpleNamespace(
            score_ocr_lock=asyncio.Lock(),
            score_recognizer=SimpleNamespace(recognize=self.recognize),
            _is_group_enabled=lambda group_id: group_id != "disabled",
        )

    async def test_attached_image(self):
        from astrbot.api.message_components import Image

        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                message=[Image.fromFileSystem("score.jpg")], group_id=""
            ),
            plain_result=lambda text: text,
        )
        with patch.object(
            Image, "convert_to_file_path", AsyncMock(return_value="score.jpg")
        ):
            result = [text async for text in type(self).handler(self.plugin, event)]
        self.assertEqual(result, ["recognized"])
        self.recognize.assert_called_once_with(Path("score.jpg"), [])

    async def test_quoted_image(self):
        from astrbot.api.message_components import Image, Reply

        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                message=[Reply(id="1", chain=[Image.fromFileSystem("quoted.jpg")])],
                group_id="",
            ),
            plain_result=lambda text: text,
        )
        with patch.object(
            Image, "convert_to_file_path", AsyncMock(return_value="quoted.jpg")
        ):
            result = [text async for text in type(self).handler(self.plugin, event)]
        self.assertEqual(result, ["recognized"])
        self.recognize.assert_called_once_with(Path("quoted.jpg"), [])

    async def test_no_image_and_disabled_group(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(message=[], group_id=""),
            plain_result=lambda text: text,
        )
        self.assertIn(
            "mai识分",
            [text async for text in type(self).handler(self.plugin, event)][0],
        )
        event.message_obj.group_id = "disabled"
        self.assertEqual(
            [text async for text in type(self).handler(self.plugin, event)], []
        )
        self.recognize.assert_not_called()

    def test_command_does_not_capture_other_bots_shifen_command(self):
        self.assertIsNotNone(re.fullmatch(self.pattern, "mai识分"))
        self.assertIsNone(re.fullmatch(self.pattern, "识分"))
        self.assertIsNone(re.fullmatch(self.pattern, "mai识分 12345"))


@unittest.skipUnless(
    os.environ.get("MAIBOT_SCORE_SAMPLES"), "Local score samples were not configured"
)
class LocalScoreImageTests(unittest.TestCase):
    def test_user_supplied_images(self):
        """Validate real images without tracking screenshots or downloading song data.

        Returns:
            None. Asserts the four manually verified image results.
        """
        from astrbot_plugin_maibot.libraries.maimaidx_score_recognition import (
            ScoreRecognizer,
        )

        root = Path(os.environ["MAIBOT_SCORE_SAMPLES"])
        catalog = json.loads((root / "music_data.json").read_text(encoding="utf-8"))
        musics = [
            SimpleNamespace(
                **{
                    **item,
                    "charts": [SimpleNamespace(**chart) for chart in item["charts"]],
                }
            )
            for item in catalog
        ]
        recognizer = ScoreRecognizer(
            root.parent.parent / "plugin_data" / "astrbot_plugin_maibot" / "ocr"
        )
        cases = (
            ("公众号内成绩详情截图.jpg", "11661", 3136, BreakDetails(6, 2, 2, 0, 0)),
            ("机台照片.jpg", "11167", 2365, BreakDetails(15, 4, 0, 1, 0)),
            ("adx结算详细版.jpg", "11816", 2112, BreakDetails(16, 0)),
            ("adx结算简略版.jpg", "233", 1864, None),
        )
        for filename, song_id, dx_score, details in cases:
            with self.subTest(filename=filename):
                result = recognizer.recognize(root / filename, musics)
                self.assertEqual(result.music_id, song_id)
                self.assertEqual(result.dx_score, dx_score)
                if details is None:
                    self.assertEqual(result.aggregate, Judgements(429, 275, 27, 1, 3))
                    self.assertEqual(result.rows, {})
                else:
                    self.assertEqual(result.analysis.status, "unique")
                    self.assertEqual(result.analysis.candidates, [details])


if __name__ == "__main__":
    unittest.main()
