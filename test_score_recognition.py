"""Offline regression tests for maimai score reconstruction and message inputs."""

import ast
import asyncio
from dataclasses import replace
import json
from fractions import Fraction
import os
from pathlib import Path
import re
import tempfile
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
    ScoreRecognizer,
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

    def test_missing_last_row_label_keeps_unread_cells_unknown(self):
        tokens = [
            t
            for t in self.tokens
            if t.text != "BREAK" and not (t.y == 343 and t.x in (570, 660))
        ]
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertEqual(result.partial_rows["BREAK"], [7, 3, 0, None, None])
        self.assertNotIn("BREAK", result.rows)

    def test_missing_critical_header_still_detects_five_columns(self):
        result = parse_ocr_tokens(
            [t for t in self.tokens if t.text != "CRIT."], self.catalog
        )
        self.assertEqual(result.rows, self.rows)

    def test_critical_perfect_misread_as_perfect_does_not_drop_a_column(self):
        rows = {
            "TAP": Judgements(668, 327, 20, 1, 1),
            "HOLD": Judgements(76, 26, 1, 0, 0),
            "SLIDE": Judgements(90, 0, 0, 0, 0),
            "TOUCH": Judgements(75, 0, 0, 0, 0),
            "BREAK": Judgements(59, 12, 1, 0, 0),
        }
        tokens = [
            t
            for t in table_tokens(rows, "100.5782")
            if t.text not in ("CRIT.", "PERF.")
        ] + [token("PERFECT", 300, 130)]
        result = parse_ocr_tokens(tokens, [music(rows)])
        self.assertFalse(result.merged_perfect)
        self.assertEqual(result.rows, rows)
        self.assertEqual(result.analysis.candidates, [BreakDetails(8, 4, 1)])

    def test_four_columns_with_a_counter_beyond_miss_remain_four_columns(self):
        rows = {name: Judgements(None, 10, 0, 0, 0) for name in NOTE_TYPES}
        tokens = table_tokens(rows, "101.0000", merged=True)
        tokens.extend(token("12", 750, 175 + 42 * index) for index in range(5))
        result = parse_ocr_tokens(tokens, [music(rows)])
        self.assertTrue(result.merged_perfect)
        self.assertEqual(result.rows, rows)

    def test_five_visible_columns_survive_all_header_omission_combinations(self):
        headers = ("CRIT.", "PERF.", "GREAT", "GOOD", "MISS")
        for mask in range(32):
            tokens = [
                t
                for t in self.tokens
                if t.y != 130 or mask & (1 << headers.index(t.text))
            ]
            with self.subTest(mask=mask):
                result = parse_ocr_tokens(tokens, self.catalog)
                self.assertFalse(result.merged_perfect)
                self.assertEqual(result.rows, self.rows)

    def test_columns_are_recovered_when_every_row_misses_a_different_cell(self):
        tokens = [
            t
            for t in self.tokens
            if t.y != 130
            and not any(
                t.y == 175 + row * 42 and t.x == 300 + (4 - row) * 90
                for row in range(5)
            )
        ]
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertFalse(result.merged_perfect)
        self.assertEqual(result.rows, self.rows)
        self.assertEqual(result.analysis.status, "unique")

    def test_current_achievement_region_excludes_personal_best_and_accepts_missing_percent(
        self,
    ):
        tokens = [t for t in self.tokens if "%" not in t.text]
        tokens.extend([token("达成率", 280, 65, width=80), token("100.0000%", 400, 65)])
        self.assertIsNone(parse_ocr_tokens(tokens, self.catalog).achievement)
        result = parse_ocr_tokens(
            tokens + [token("100.875000", 300, 100, width=200, height=50)], self.catalog
        )
        self.assertEqual(result.achievement, "100.875000")
        self.assertEqual(result.analysis.status, "unique")

    def test_difficulty_is_not_assumed_when_absent(self):
        result = parse_ocr_tokens(
            [t for t in self.tokens if t.text != "Master"], self.catalog
        )
        self.assertIsNone(result.music_id)
        self.assertIsNone(result.difficulty)

    def test_exact_title_avoids_fuzzy_catalog_comparisons(self):
        catalog = [
            music(self.rows, f"Unrelated song {index}", str(index))
            for index in range(100)
        ] + self.catalog
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_score_recognition.SequenceMatcher"
        ) as matcher:
            result = parse_ocr_tokens(self.tokens, catalog)
        self.assertEqual(result.music_id, "12345")
        self.assertEqual(result.rows, self.rows)
        matcher.assert_not_called()

    def test_fuzzy_title_still_matches_after_quick_similarity_bounds(self):
        tokens = [
            replace(t, text="Test s0ng") if t.text == "Test song" else t
            for t in self.tokens
        ]
        result = parse_ocr_tokens(
            tokens, [music(self.rows, "Other title", "54321")] + self.catalog
        )
        self.assertEqual(result.music_id, "12345")
        self.assertEqual(result.analysis.status, "unique")

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

    def test_tilted_table_keeps_each_counter_in_its_own_row(self):
        rows = {
            "TAP": Judgements(767, 626, 128, 21, 14),
            "HOLD": Judgements(92, 45, 6, 0, 0),
            "SLIDE": Judgements(162, 0, 2, 3, 1),
            "TOUCH": Judgements(92, 0, 1, 0, 0),
            "BREAK": Judgements(140, 65, 14, 0, 2),
        }
        for slope in (0.08, -0.08):
            tokens = [
                replace(
                    t,
                    top=t.top + slope * t.x,
                    bottom=t.bottom + slope * t.x,
                    slope=slope,
                )
                for t in table_tokens(rows, "98.3528")
            ]
            with self.subTest(slope=slope):
                result = parse_ocr_tokens(tokens, [music(rows)])
                self.assertEqual(result.rows, rows)
                self.assertEqual(result.analysis.dx_score, 5382)
                self.assertIn(result.analysis.status, ("unique", "ambiguous"))

    def test_larger_incomplete_score_does_not_fall_back_to_personal_best(self):
        tokens = [t for t in self.tokens if "%" not in t.text]
        tokens.extend(
            [
                token(".875000%", 300, 75, width=220, height=50),
                token("100.0000%", 300, 105, width=120),
            ]
        )
        self.assertIsNone(parse_ocr_tokens(tokens, self.catalog).achievement)

    def test_glare_can_hide_headers_without_hiding_five_numeric_columns(self):
        for keep_miss in (False, True):
            tokens = [
                t for t in self.tokens if t.y != 130 or keep_miss and t.text == "MISS"
            ]
            with self.subTest(keep_miss=keep_miss):
                result = parse_ocr_tokens(tokens, self.catalog)
                self.assertEqual(result.rows, self.rows)
                self.assertEqual(result.analysis.status, "unique")
        merged = {name: Judgements(None, 10, 0, 0, 0) for name in NOTE_TYPES}
        tokens = [
            t for t in table_tokens(merged, "100.0000", merged=True) if t.y != 130
        ]
        self.assertEqual(parse_ocr_tokens(tokens, [music(merged)]).rows, {})

    def test_split_percentage_is_joined_only_on_the_same_line(self):
        tokens = [t for t in self.tokens if "%" not in t.text]
        for prefix, suffix in (
            ("100", ".875000%"),
            ("100.", "875000%"),
            ("100.", ".875000%"),
        ):
            with self.subTest(prefix=prefix, suffix=suffix):
                result = parse_ocr_tokens(
                    tokens
                    + [
                        token(prefix, 240, 75, width=100, height=50),
                        token(suffix, 380, 75, width=150, height=50),
                    ],
                    self.catalog,
                )
                self.assertEqual(result.achievement, "100.875000")
                self.assertEqual(result.analysis.candidates, [BreakDetails(1, 2)])
        result = parse_ocr_tokens(
            tokens
            + [
                token("100.", 240, 40, width=100, height=50),
                token("875000%", 380, 75, width=150, height=50),
            ],
            self.catalog,
        )
        self.assertIsNone(result.achievement)
        result = parse_ocr_tokens(
            tokens
            + [
                token("100.", 240, 75, width=200, height=185),
                token("875000%", 380, 75, width=230, height=185),
            ],
            self.catalog,
        )
        self.assertEqual(result.achievement, "100.875000")

    def test_blank_touch_cells_are_zero_only_when_the_chart_has_no_touch(self):
        rows = {**self.rows, "TOUCH": Judgements(0, 0, 0, 0, 0)}
        tokens = [
            t
            for t in table_tokens(rows, "100.875000")
            if not (t.y == 301 and t.text != "TOUCH")
        ]
        catalog = music(rows)
        catalog.charts = [SimpleNamespace(notes=(10, 10, 10, 10)) for _ in range(4)]
        result = parse_ocr_tokens(tokens, [catalog])
        self.assertEqual(result.rows["TOUCH"], rows["TOUCH"])
        self.assertEqual(result.partial_rows["TOUCH"], [0] * 5)
        self.assertEqual(result.analysis.status, "unique")
        result = parse_ocr_tokens(tokens, self.catalog)
        self.assertNotIn("TOUCH", result.rows)
        self.assertEqual(result.partial_rows["TOUCH"], [None] * 5)
        result = parse_ocr_tokens(tokens + [token("1", 300, 301)], [catalog])
        self.assertNotIn("TOUCH", result.rows)
        self.assertEqual(result.partial_rows["TOUCH"][0], 1)

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


class OCRRecoveryTests(unittest.TestCase):
    def test_precision_fallback_reuses_models_and_restores_fast_settings(self):
        """Retry failed scoring with original settings, restoring them on errors."""
        from PIL import Image
        from rapidocr import RapidOCR

        rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["BREAK"] = Judgements(7, 3, 0, 0, 0)
        valid = parse_ocr_tokens(table_tokens(rows, "100.875000"), [music(rows)])
        incomplete = parse_ocr_tokens([], [])
        engine = Mock(spec=RapidOCR)
        engine.text_det = SimpleNamespace(limit_type="max", limit_side_len=1280)
        engine.text_rec = SimpleNamespace(rec_image_shape=[3, 48, 128])
        engine.cfg = SimpleNamespace(Rec=SimpleNamespace(rec_img_shape=[3, 48, 128]))
        settings = []

        def stage(source, catalog):
            settings.append(
                (
                    engine.text_det.limit_type,
                    engine.text_det.limit_side_len,
                    list(engine.text_rec.rec_image_shape),
                )
            )
            return incomplete if len(settings) == 1 else valid

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            Image.new("RGB", (800, 500), "white").save(path)
            recognizer = ScoreRecognizer(Path(directory))
            recognizer.engine = engine
            recognizer._recognize_source = Mock(side_effect=stage)
            result = recognizer.recognize(path, [music(rows)])
            self.assertIs(result, valid)
            self.assertIs(recognizer.engine, engine)
            self.assertEqual(
                settings, [("max", 1280, [3, 48, 128]), ("min", 736, [3, 48, 320])]
            )
            recognizer._recognize_source = Mock(
                side_effect=[incomplete, RuntimeError("failed")]
            )
            with self.assertRaises(RuntimeError):
                recognizer.recognize(path, [music(rows)])
        self.assertEqual(engine.text_det.limit_type, "max")
        self.assertEqual(engine.text_det.limit_side_len, 1280)
        self.assertEqual(engine.text_rec.rec_image_shape, [3, 48, 128])
        self.assertEqual(engine.cfg.Rec.rec_img_shape, [3, 48, 128])

    def test_first_pass_skips_recovery_only_with_verified_dx_and_confident_cells(self):
        """Avoid a second OCR pass while retaining recovery for uncertain input."""
        import numpy as np
        from PIL import Image

        rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["BREAK"] = Judgements(7, 3, 0, 0, 0)
        observations = table_tokens(rows, "100.875000") + [
            token("DX SCORE", 400, 400),
            token("147/150", 400, 435),
        ]
        for variant, calls in (("verified", 1), ("low_confidence", 2), ("no_dx", 2)):
            with self.subTest(variant=variant):
                tokens = [
                    replace(t, score=0.7)
                    if variant == "low_confidence" and t.x == 570 and t.y == 175
                    else t
                    for t in observations
                    if variant != "no_dx" or t.y < 400
                ]
                outputs = [
                    SimpleNamespace(
                        boxes=np.array(
                            [
                                [
                                    [t.left, t.top],
                                    [t.right, t.top],
                                    [t.right, t.bottom],
                                    [t.left, t.bottom],
                                ]
                                for t in tokens
                            ]
                        ),
                        txts=tuple(t.text for t in tokens),
                        scores=tuple(t.score for t in tokens),
                    ),
                    SimpleNamespace(boxes=None, txts=None, scores=None),
                ]
                if variant == "low_confidence":
                    outputs[1] = SimpleNamespace(txts=("0",), scores=(0.99,))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "result.png"
                    Image.new("RGB", (800, 500), "white").save(path)
                    recognizer = ScoreRecognizer(Path(directory))
                    recognizer.engine = Mock(side_effect=outputs)
                    result = recognizer.recognize(path, [music(rows)])
                self.assertEqual(result.rows, rows)
                self.assertEqual(result.analysis.status, "unique")
                self.assertEqual(recognizer.engine.call_count, calls)

    def test_enlarged_partial_number_keeps_the_observed_full_counter(self):
        """Do not replace a confident 378 with a smaller detection of just 78."""
        import numpy as np
        from PIL import Image

        rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["TAP"] = Judgements(378, 222, 21, 2, 0)
        value = calculate_achievement(rows, BreakDetails())
        units = round(value * 10000)
        observations = table_tokens(rows, f"{units // 10000}.{units % 10000:04d}")
        # The enlarged fixture covers source (97, 82) to (800, 376).
        scale = 1800 / 703
        partial = token(
            "78",
            (310 - 97) * scale,
            (175 - 82) * scale,
            width=20 * scale,
            height=12 * scale,
        )
        outputs = []
        for tokens in (observations, [replace(partial, score=0.98)]):
            outputs.append(
                SimpleNamespace(
                    boxes=np.array(
                        [
                            [
                                [t.left, t.top],
                                [t.right, t.top],
                                [t.right, t.bottom],
                                [t.left, t.bottom],
                            ]
                            for t in tokens
                        ]
                    ),
                    txts=tuple(t.text for t in tokens),
                    scores=tuple(t.score for t in tokens),
                )
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            Image.new("RGB", (800, 500), "white").save(path)
            recognizer = ScoreRecognizer(Path(directory))
            recognizer.engine = Mock(side_effect=outputs)
            result = recognizer.recognize(path, [music(rows)])
        self.assertEqual(result.rows, rows)
        self.assertEqual(result.analysis.status, "unique")
        self.assertEqual(recognizer.engine.call_count, 2)

    def test_missing_break_label_does_not_crop_out_last_row_or_skip_its_cells(self):
        """Keep the visible last row and reread its missing zeroes from pixels."""
        import numpy as np
        from PIL import Image

        rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["BREAK"] = Judgements(7, 3, 0, 0, 0)
        observations = [
            t
            for t in table_tokens(rows, "100.875000")
            if t.text != "BREAK" and not (t.y == 343 and t.x in (570, 660))
        ]
        outputs = []
        for tokens in (observations, []):
            outputs.append(
                SimpleNamespace(
                    boxes=np.array(
                        [
                            [
                                [t.left, t.top],
                                [t.right, t.top],
                                [t.right, t.bottom],
                                [t.left, t.bottom],
                            ]
                            for t in tokens
                        ]
                    ),
                    txts=tuple(t.text for t in tokens),
                    scores=tuple(t.score for t in tokens),
                )
            )
        outputs.extend(SimpleNamespace(txts=("0",), scores=(0.99,)) for _ in range(2))
        outputs[1:1] = [SimpleNamespace(txts=(), scores=()) for _ in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            picture = Image.new("RGB", (800, 500), "white")
            picture.paste("black", (0, 340, 800, 350))
            picture.save(path)
            recognizer = ScoreRecognizer(Path(directory))
            recognizer.engine = Mock(side_effect=outputs)
            result = recognizer.recognize(path, [music(rows)])
        self.assertTrue(np.any(recognizer.engine.call_args_list[3].args[0] == 0))
        self.assertEqual(result.rows, rows)
        self.assertEqual(result.analysis.status, "unique")
        self.assertEqual(recognizer.engine.call_count, 6)

    def test_missing_large_score_is_retried_below_the_achievement_label(self):
        """Read the score region even when only the personal best was detected."""
        import numpy as np
        from PIL import Image

        rows = {
            "TAP": Judgements(378, 222, 21, 2, 0),
            "HOLD": Judgements(54, 23, 0, 0, 0),
            "SLIDE": Judgements(132, 0, 1, 0, 0),
            "TOUCH": Judgements(93, 0, 0, 0, 1),
            "BREAK": Judgements(65, 33, 2, 0, 0),
        }
        observations = [t for t in table_tokens(rows, "100.4033") if "%" not in t.text]
        observations.extend(
            [
                token("达成率", 280, 65, width=80),
                token("100.1442%", 400, 65),
                token("DX SCORE", 400, 400),
                token("2746/3081", 400, 435),
            ]
        )
        outputs = []
        for tokens in (observations, [], [token("100.1442%", 30, 10)]):
            outputs.append(
                SimpleNamespace(
                    boxes=np.array(
                        [
                            [
                                [t.left, t.top],
                                [t.right, t.top],
                                [t.right, t.bottom],
                                [t.left, t.bottom],
                            ]
                            for t in tokens
                        ]
                    ),
                    txts=tuple(t.text for t in tokens),
                    scores=tuple(t.score for t in tokens),
                )
            )
        outputs.insert(2, SimpleNamespace(txts=("100.1442%",), scores=(0.99,)))
        outputs.append(SimpleNamespace(txts=("100.4033%",), scores=(0.99,)))
        del outputs[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            Image.new("RGB", (1000, 650), "white").save(path)
            recognizer = ScoreRecognizer(Path(directory))
            recognizer.engine = Mock(side_effect=outputs)
            result = recognizer.recognize(path, [music(rows)])
        self.assertEqual(result.achievement, "100.4033")
        self.assertEqual(result.analysis.candidates, [BreakDetails(31, 2, 2)])
        self.assertEqual(recognizer.engine.call_count, 4)

    def test_failed_percentage_reread_does_not_select_personal_best(self):
        """Require new pixels to recover the current score instead of its best."""
        import numpy as np
        from PIL import Image

        rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["BREAK"] = Judgements(7, 3, 0, 0, 0)
        observations = table_tokens(rows, "101.000000") + [
            token("100.875000%", 300, 105),
            token("DX SCORE", 400, 400),
            token("147/150", 400, 435),
        ]
        outputs = []
        for tokens in (observations, [], [token("100.875000%", 100, 25)]):
            outputs.append(
                SimpleNamespace(
                    boxes=np.array(
                        [
                            [
                                [t.left, t.top],
                                [t.right, t.top],
                                [t.right, t.bottom],
                                [t.left, t.bottom],
                            ]
                            for t in tokens
                        ]
                    ),
                    txts=tuple(t.text for t in tokens),
                    scores=tuple(t.score for t in tokens),
                )
            )
        # The rec-only pass fails; the following detection pass reads the score.
        outputs.insert(2, SimpleNamespace(txts=("101.000000%",), scores=(0.2,)))
        del outputs[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            Image.new("RGB", (800, 500), "white").save(path)
            recognizer = ScoreRecognizer(Path(directory))
            recognizer.engine = Mock(side_effect=outputs)
            result = recognizer.recognize(path, [music(rows)])
            outside = SimpleNamespace(
                boxes=np.array([[[80, -10], [120, -10], [120, 8], [80, 8]]]),
                txts=("100.875000%",),
                scores=(0.99,),
            )
            recognizer.engine = Mock(
                side_effect=[
                    outputs[0],
                    outputs[1],
                    outside,
                    SimpleNamespace(boxes=None, txts=None, scores=None),
                    outputs[1],
                    outside,
                ]
            )
            failed = recognizer.recognize(path, [music(rows)])
        self.assertEqual(result.achievement, "100.875000")
        self.assertEqual(result.analysis.status, "unique")
        self.assertEqual(recognizer.engine.call_count, 6)
        self.assertEqual(failed.achievement, "101.000000")
        self.assertEqual(failed.analysis.status, "inconsistent")

    def test_mismatched_row_is_reread_from_pixels_without_changing_other_rows(self):
        """Retry an observed 6 as a glyph, rather than forcing a catalog remainder."""
        import numpy as np
        from PIL import Image

        rows = {name: Judgements(10, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["HOLD"] = Judgements(18, 24, 9, 1, 1)
        value = calculate_achievement(rows, BreakDetails())
        units = value.numerator * 10000 // value.denominator
        observations = table_tokens(rows, f"{units // 10000}.{units % 10000:04d}")
        observations.extend([token("DX SCORE", 400, 400), token("231/279", 400, 435)])
        observations = [
            replace(t, text="6") if t.x == 480 and t.y == 217 else t
            for t in observations
        ]
        outputs = []
        for tokens in (observations, []):
            outputs.append(
                SimpleNamespace(
                    boxes=np.array(
                        [
                            [
                                [t.left, t.top],
                                [t.right, t.top],
                                [t.right, t.bottom],
                                [t.left, t.bottom],
                            ]
                            for t in tokens
                        ]
                    ),
                    txts=tuple(t.text for t in tokens),
                    scores=tuple(t.score for t in tokens),
                )
            )
        outputs.extend(
            SimpleNamespace(txts=(str(value),), scores=(0.99,))
            for value in (18, 24, 9, 1, 1)
        )
        del outputs[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            Image.new("RGB", (800, 500), "white").save(path)
            recognizer = ScoreRecognizer(Path(directory))
            recognizer.engine = Mock(side_effect=outputs)
            result = recognizer.recognize(path, [music(rows)])
        self.assertEqual(result.rows, rows)
        self.assertEqual(result.analysis.status, "unique")
        self.assertEqual(recognizer.engine.call_count, 6)


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
        cls.mai = SimpleNamespace(total_list=[])
        namespace = {
            "__package__": "astrbot_plugin_maibot",
            "AstrMessageEvent": object,
            "asyncio": asyncio,
            "Path": Path,
            "mai": cls.mai,
            "logger": Mock(),
        }
        exec(
            compile(ast.Module(body=[handler], type_ignores=[]), "main.py", "exec"),
            namespace,
        )
        cls.handler = namespace["mai_score_recognition"]

    def setUp(self):
        from astrbot.api.event import MessageEventResult
        from astrbot_plugin_maibot.libraries.maimaidx_score_recognition import (
            RecognizedScore,
        )

        rows = {name: Judgements(0, 0, 0, 0, 0) for name in NOTE_TYPES}
        rows["TAP"] = Judgements(47, 3, 0, 0, 0)
        self.mai.total_list = [music(rows)]
        self.score = RecognizedScore(
            "official",
            achievement="100.0000",
            music_id="12345",
            title="Test song",
            difficulty=3,
            rows=rows,
            analysis=analyse_judgements(rows, "100.0000"),
        )
        self.recognize = Mock(return_value=self.score)
        drawing = patch(
            "astrbot_plugin_maibot.libraries.maimaidx_score_image.render_score_image",
            return_value=b"test image",
        )
        self.render = drawing.start()
        self.addCleanup(drawing.stop)
        self.plugin = SimpleNamespace(
            score_ocr_lock=asyncio.Lock(),
            score_recognizer=SimpleNamespace(recognize=self.recognize),
            _is_group_enabled=lambda group_id: group_id != "disabled",
        )
        self.event = SimpleNamespace(
            message_obj=SimpleNamespace(message=[], group_id=""),
            message_str="mai识分",
            plain_result=lambda text: MessageEventResult().message(text),
            chain_result=lambda chain: chain,
        )

    async def test_attached_image(self):
        from astrbot.api.message_components import Image

        event = self.event
        event.message_obj.message = [Image.fromFileSystem("score.jpg")]
        with patch.object(
            Image, "convert_to_file_path", AsyncMock(return_value="score.jpg")
        ):
            result = [text async for text in type(self).handler(self.plugin, event)]
        self.assertIsInstance(result[0][0], Image)
        self.assertTrue(result[0][0].file.startswith("base64://"))
        self.recognize.assert_called_once_with(Path("score.jpg"), self.mai.total_list)

    async def test_quoted_image(self):
        from astrbot.api.message_components import Image, Reply

        event = self.event
        event.message_obj.message = [
            Reply(id="1", chain=[Image.fromFileSystem("quoted.jpg")])
        ]
        with patch.object(
            Image, "convert_to_file_path", AsyncMock(return_value="quoted.jpg")
        ):
            result = [text async for text in type(self).handler(self.plugin, event)]
        self.assertIsInstance(result[0][0], Image)
        self.recognize.assert_called_once_with(Path("quoted.jpg"), self.mai.total_list)

    async def test_no_image_and_disabled_group(self):
        event = self.event
        self.assertIn(
            "mai识分",
            [text async for text in type(self).handler(self.plugin, event)][0]
            .chain[0]
            .text,
        )
        event.message_obj.group_id = "disabled"
        self.assertEqual(
            [text async for text in type(self).handler(self.plugin, event)], []
        )
        self.recognize.assert_not_called()

    async def test_manual_command_generates_image_without_ocr(self):
        from astrbot.api.message_components import Image

        self.event.message_str = (
            "  mai识分 id=12345 ach=100.0000 diff=紫 tap=47/3/0/0/0 "
            "hold=0/0/0/0/0 slide=0/0/0/0/0 touch=0/0/0/0/0 break=0/0/0/0/0"
        )
        result = [item async for item in type(self).handler(self.plugin, self.event)]
        self.assertIsInstance(result[0][0], Image)
        self.recognize.assert_not_called()
        self.render.assert_called_once()

    async def test_partial_ocr_preserves_known_values_in_completion(self):
        from astrbot.api.message_components import Image

        self.score.rows = {}
        self.score.partial_rows = {"TAP": [47, None, None, 0, 0]}
        self.score.analysis = None
        self.event.message_obj.message = [Image.fromFileSystem("partial.jpg")]
        with patch.object(
            Image, "convert_to_file_path", AsyncMock(return_value="partial.jpg")
        ):
            result = [
                item async for item in type(self).handler(self.plugin, self.event)
            ]
        self.assertIn("tap=47/?/?/0/0", result[0].chain[0].text)
        self.assertIn("id=12345 ach=100.0000", result[0].chain[0].text)
        self.render.assert_not_called()

    async def test_missing_drawing_font_keeps_recognized_data_editable(self):
        from astrbot.api.message_components import Image

        self.render.side_effect = OSError("font unavailable")
        self.event.message_obj.message = [Image.fromFileSystem("score.jpg")]
        with patch.object(
            Image, "convert_to_file_path", AsyncMock(return_value="score.jpg")
        ):
            result = [
                item async for item in type(self).handler(self.plugin, self.event)
            ]
        self.assertIn("绘图字体", result[0].chain[0].text)
        self.assertIn("tap=47/3/0/0/0", result[0].chain[0].text)

    def test_command_does_not_capture_other_bots_shifen_command(self):
        self.assertIsNotNone(re.fullmatch(self.pattern, "mai识分"))
        self.assertIsNone(re.fullmatch(self.pattern, "识分"))
        self.assertIsNone(re.fullmatch(self.pattern, "mai识分 12345"))
        self.assertIsNotNone(
            re.fullmatch(self.pattern, "mai识分 id=11167 ach=100.2098")
        )


@unittest.skipUnless(
    os.environ.get("MAIBOT_SCORE_SAMPLES"), "Local score samples were not configured"
)
class LocalScoreImageTests(unittest.TestCase):
    def test_user_supplied_images(self):
        """Validate real images without tracking screenshots or downloading song data.

        Returns:
            None. Asserts the available manually verified image results.
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
        if (root / "公众号标准成绩详情截图.jpg").is_file():
            cases += (("公众号标准成绩详情截图.jpg", "348", 2246, BreakDetails()),)
        if (root / "倾斜机台照片.jpg").is_file():
            cases += (
                ("倾斜机台照片.jpg", "11820", 5382, BreakDetails(49, 16, 11, 2, 1)),
            )
        if (root / "反向倾斜机台照片.jpg").is_file():
            cases += (
                ("反向倾斜机台照片.jpg", "11106", 2932, BreakDetails(8, 2, 1, 1, 0)),
            )
        if (root / "反光机台红谱照片.jpg").is_file():
            cases += (("反光机台红谱照片.jpg", "11820", 3656, BreakDetails(8, 4, 1)),)
        if (root / "带历史最佳机台照片.jpg").is_file():
            cases += (
                ("带历史最佳机台照片.jpg", "11382", 2746, BreakDetails(31, 2, 2)),
            )
        if (root / "ADX深色判定详情截图.jpg").is_file():
            cases += (("ADX深色判定详情截图.jpg", "11509", 2275, BreakDetails(4)),)
        for filename, song_id, dx_score, details in cases:
            with self.subTest(filename=filename):
                result = recognizer.recognize(root / filename, musics)
                self.assertEqual(result.music_id, song_id)
                self.assertEqual(result.dx_score, dx_score)
                if filename == "adx结算简略版.jpg":
                    self.assertEqual(result.aggregate, Judgements(429, 275, 27, 1, 3))
                    self.assertEqual(result.rows, {})
                elif details is not None:
                    self.assertEqual(result.analysis.status, "unique")
                    self.assertEqual(result.analysis.candidates, [details])
                if filename == "公众号标准成绩详情截图.jpg":
                    self.assertEqual(result.achievement, "99.1893")
                    self.assertEqual(result.rows["HOLD"], Judgements(18, 24, 9, 1, 1))
                    self.assertEqual(result.rows["TOUCH"], Judgements(0, 0, 0, 0, 0))
                elif filename == "倾斜机台照片.jpg":
                    self.assertEqual(result.achievement, "98.3528")
                    self.assertEqual(
                        result.rows,
                        {
                            "TAP": Judgements(767, 626, 128, 21, 14),
                            "HOLD": Judgements(92, 45, 6, 0, 0),
                            "SLIDE": Judgements(162, 0, 2, 3, 1),
                            "TOUCH": Judgements(92, 0, 1, 0, 0),
                            "BREAK": Judgements(140, 65, 14, 0, 2),
                        },
                    )
                    self.assertIn(result.analysis.status, ("unique", "ambiguous"))
                elif filename == "反向倾斜机台照片.jpg":
                    self.assertEqual(result.achievement, "97.3942")
                    self.assertEqual(
                        result.rows,
                        {
                            "TAP": Judgements(453, 320, 89, 20, 13),
                            "HOLD": Judgements(21, 14, 2, 0, 0),
                            "SLIDE": Judgements(92, 0, 2, 0, 0),
                            "TOUCH": Judgements(125, 2, 0, 1, 3),
                            "BREAK": Judgements(24, 10, 2, 0, 1),
                        },
                    )
                    self.assertIn(result.analysis.status, ("unique", "ambiguous"))

                elif filename == "反光机台红谱照片.jpg":
                    self.assertEqual(result.achievement, "100.5782")
                    self.assertEqual(result.difficulty, 2)
                    self.assertEqual(
                        result.rows,
                        {
                            "TAP": Judgements(668, 327, 20, 1, 1),
                            "HOLD": Judgements(76, 26, 1, 0, 0),
                            "SLIDE": Judgements(90, 0, 0, 0, 0),
                            "TOUCH": Judgements(75, 0, 0, 0, 0),
                            "BREAK": Judgements(59, 12, 1, 0, 0),
                        },
                    )
                elif filename == "带历史最佳机台照片.jpg":
                    self.assertEqual(result.achievement, "100.4033")
                    self.assertEqual(
                        result.rows,
                        {
                            "TAP": Judgements(378, 222, 21, 2, 0),
                            "HOLD": Judgements(54, 23, 0, 0, 0),
                            "SLIDE": Judgements(132, 0, 1, 0, 0),
                            "TOUCH": Judgements(93, 0, 0, 0, 1),
                            "BREAK": Judgements(65, 33, 2, 0, 0),
                        },
                    )
                elif filename == "ADX深色判定详情截图.jpg":
                    self.assertEqual(result.achievement, "100.819403")
                    self.assertEqual(
                        result.rows,
                        {
                            "TAP": Judgements(398, 162, 10, 0, 0),
                            "HOLD": Judgements(35, 8, 0, 0, 0),
                            "SLIDE": Judgements(54, 0, 0, 0, 0),
                            "TOUCH": Judgements(95, 0, 0, 0, 0),
                            "BREAK": Judgements(57, 4, 0, 0, 0),
                        },
                    )
                if filename in ("反光机台红谱照片.jpg", "带历史最佳机台照片.jpg"):
                    from PIL import Image

                    with tempfile.TemporaryDirectory() as directory:
                        compressed_path = Path(directory) / "compressed.jpg"
                        with Image.open(root / filename) as picture:
                            picture.convert("RGB").resize(
                                (1024, round(picture.height * 1024 / picture.width))
                            ).save(compressed_path, quality=80)
                        compressed = recognizer.recognize(compressed_path, musics)
                    self.assertEqual(compressed.music_id, result.music_id)
                    self.assertEqual(compressed.difficulty, result.difficulty)
                    self.assertEqual(compressed.achievement, result.achievement)
                    self.assertEqual(compressed.rows, result.rows)
                    self.assertFalse(compressed.merged_perfect)
                    self.assertEqual(compressed.analysis.candidates, [details])


if __name__ == "__main__":
    unittest.main()
