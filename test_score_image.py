"""Verify manual score recovery, exact image losses and editable QQ replies."""

from fractions import Fraction
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from PIL import Image
from astrbot.api.event import MessageEventResult

from astrbot_plugin_maibot.command.mai_score_input import score_completion_reply
from astrbot_plugin_maibot.libraries.maimaidx_score import (
    NOTE_TYPES,
    BreakDetails,
    Judgements,
    calculate_achievement,
)
from astrbot_plugin_maibot.libraries.maimaidx_score_image import (
    judgement_loss,
    render_score_image,
)
from astrbot_plugin_maibot.libraries.maimaidx_score_input import parse_manual_score


COMMAND = "id=11167 ach=100.2098 tap=453/226/17/0/1 hold=28/12/0/0/0 slide=93/0/0/1/0 touch=19/0/0/0/0 break=18/19/1/0/0"
ROWS = {
    "TAP": Judgements(453, 226, 17, 0, 1),
    "HOLD": Judgements(28, 12, 0, 0, 0),
    "SLIDE": Judgements(93, 0, 0, 1, 0),
    "TOUCH": Judgements(19, 0, 0, 0, 0),
    "BREAK": Judgements(18, 19, 1, 0, 0),
}
MUSIC = SimpleNamespace(
    id="11167",
    title="BIRTH",
    type="DX",
    ds=[1.0, 2.0, 3.0, 14.5],
    level=["1", "2", "3", "14"],
    charts=[SimpleNamespace(notes=(10, 1, 1, 0, 1)) for _ in range(3)]
    + [SimpleNamespace(notes=tuple(ROWS[name].total for name in NOTE_TYPES))],
    basic_info=SimpleNamespace(
        artist="Iliiiiiiiii", bpm=176, version="maimai でらっくす Splash"
    ),
)


class ManualScoreTests(unittest.TestCase):
    def test_complete_command_matches_chart_and_break_bins(self):
        score = parse_manual_score(COMMAND, [MUSIC])
        self.assertEqual((score.music_id, score.difficulty), ("11167", 3))
        self.assertEqual(score.rows, ROWS)
        self.assertEqual(score.analysis.status, "unique")
        self.assertEqual(score.analysis.candidates, [BreakDetails(15, 4, 0, 1, 0)])
        self.assertEqual(score.analysis.dx_score, 2365)

    def test_missing_cells_are_preserved_and_one_cell_can_be_recovered(self):
        partial = parse_manual_score(COMMAND.replace("453/226/17", "453/?/?"), [MUSIC])
        self.assertEqual(partial.partial_rows["TAP"], [453, None, None, 0, 1])
        self.assertNotIn("TAP", partial.rows)
        recovered = parse_manual_score(
            COMMAND.replace("453/226/17", "453/?/17"), [MUSIC]
        )
        self.assertEqual(recovered.rows["TAP"], ROWS["TAP"])
        self.assertEqual(recovered.analysis.status, "unique")

    def test_manual_unknown_id_and_difficulty_can_match_exact_note_totals(self):
        score = parse_manual_score(COMMAND.replace("id=11167", "id=?"), [MUSIC])
        self.assertEqual((score.music_id, score.difficulty), ("11167", 3))

    def test_conflicting_achievement_is_not_renderable(self):
        score = parse_manual_score(COMMAND.replace("100.2098", "101.0000"), [MUSIC])
        self.assertEqual(score.analysis.status, "inconsistent")
        with self.assertRaises(ValueError):
            render_score_image(score, MUSIC)

    def test_explicit_difficulty_preserves_note_total_mismatch(self):
        score = parse_manual_score(
            COMMAND.replace("453/226/17", "454/226/17") + " diff=紫", [MUSIC]
        )
        self.assertFalse(score.title)
        self.assertIn("不一致", score.warnings[0])

    def test_invalid_or_duplicate_parameters_are_rejected(self):
        for command in (
            "id=1 id=2",
            "ach=102.0000",
            "tap=1/-2/3/4/5",
            "tap=1/2",
            "unknown=1",
            "round=2",
            "diff=unknown",
            "ach=NaN",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                parse_manual_score(command, [MUSIC])

    def test_merged_perfect_keeps_four_column_mode(self):
        parameters = "id=11167 ach=100.2098 " + " ".join(
            f"{name.lower()}={row.critical + row.perfect}/{row.great}/{row.good}/{row.miss}"
            for name, row in ROWS.items()
        )
        score = parse_manual_score(parameters, [MUSIC])
        self.assertTrue(score.merged_perfect)
        self.assertEqual(score.analysis.status, "ambiguous")
        self.assertTrue(all(row.critical is None for row in score.rows.values()))


class ScoreLossTests(unittest.TestCase):
    def test_displayed_cell_losses_sum_to_exact_achievement_loss(self):
        details = BreakDetails(15, 4, 0, 1, 0)
        total = sum(
            judgement_loss(ROWS, name, judgement)
            * getattr(ROWS[name], judgement.lower())
            for name in NOTE_TYPES[:-1]
            for judgement in ("GREAT", "GOOD", "MISS")
        )
        total += sum(
            judgement_loss(ROWS, "BREAK", judgement) * count
            for judgement, count in (("P50", 15), ("P100", 4), ("G1500", 1))
        )
        self.assertEqual(total, Fraction(101) - calculate_achievement(ROWS, details))
        self.assertAlmostEqual(
            float(judgement_loss(ROWS, "BREAK", "P50") * 15), 0.09868421052631579
        )
        self.assertEqual(judgement_loss(ROWS, "BREAK", "P100"), Fraction(1, 76))

    def test_every_break_great_good_and_miss_includes_bonus_loss(self):
        for judgement, base, bonus in (
            ("G2000", 500, 60),
            ("G1500", 1000, 60),
            ("G1250", 1250, 60),
            ("GOOD", 1500, 70),
            ("MISS", 2500, 100),
        ):
            with self.subTest(judgement=judgement):
                maximum = sum(
                    ROWS[name].total * weight
                    for name, weight in zip(NOTE_TYPES, (500, 1000, 1500, 500, 2500))
                )
                expected = Fraction(base * 100, maximum) + Fraction(bonus, 3800)
                self.assertEqual(judgement_loss(ROWS, "BREAK", judgement), expected)

    def test_render_returns_png_without_persisting_a_shared_result_file(self):
        score = parse_manual_score(COMMAND, [MUSIC])
        output = render_score_image(score, MUSIC)
        with Image.open(BytesIO(output)) as picture:
            self.assertEqual(picture.format, "PNG")
            self.assertEqual(picture.width, 1170)
            self.assertGreaterEqual(picture.height, 1500)
        long_music = SimpleNamespace(
            **vars(MUSIC) | {"title": "長い曲名 / 一个非常长的歌曲名称" * 12}
        )
        score.title = long_music.title
        self.assertTrue(render_score_image(score, long_music).startswith(b"\x89PNG"))

    def test_ambiguous_break_ranges_can_still_render(self):
        parameters = "id=11167 ach=100.2098 " + " ".join(
            f"{name.lower()}={row.critical + row.perfect}/{row.great}/{row.good}/{row.miss}"
            for name, row in ROWS.items()
        )
        score = parse_manual_score(parameters, [MUSIC])
        self.assertTrue(render_score_image(score, MUSIC).startswith(b"\x89PNG"))


class ScoreCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Prepare an isolated event with real result-chain behavior."""
        self.event = SimpleNamespace(
            get_platform_name=lambda: "onebot",
            plain_result=lambda text: MessageEventResult().message(text),
        )
        self.score = parse_manual_score(
            COMMAND.replace("453/226/17", "453/?/?"), [MUSIC]
        )

    async def test_partial_counts_become_a_round_trippable_command(self):
        result = await score_completion_reply(self.event, self.score, "请补全")
        text = result.chain[0].text
        self.assertIn("tap=453/?/?/0/1", text)
        self.assertIn("break=18/19/1/0/0", text)
        self.assertFalse(result.use_t2i_)
        self.assertFalse(result.use_markdown_)
        command = text.split("\n\n")[-1].removeprefix("mai识分 ")
        filled = parse_manual_score(command.replace("453/?/?", "453/226/17"), [MUSIC])
        self.assertEqual(filled.analysis.status, "unique")

    async def test_adx_completion_preserves_display_rounding(self):
        self.score.layout = "adx"
        result = await score_completion_reply(self.event, self.score, "请补全")
        self.assertIn("round=1", result.chain[0].text)

    async def test_qq_markdown_button_fills_input_without_sending(self):
        import botpy.message

        source = Mock(spec=botpy.message.GroupMessage)
        source.group_openid = "test-group"
        self.event.get_platform_name = lambda: "qq_official"
        self.event.message_obj = SimpleNamespace(
            raw_message=source, message_id="test-message"
        )
        sender = AsyncMock(return_value={"id": "reply-id"})
        self.event.bot = SimpleNamespace(api=SimpleNamespace(post_group_message=sender))
        self.event.stop_event = Mock()
        reply = await score_completion_reply(self.event, self.score, "请补全")
        self.assertIsNone(reply)
        payload = sender.call_args.kwargs
        self.assertEqual(
            (payload["group_openid"], payload["msg_id"]), ("test-group", "test-message")
        )
        button = payload["keyboard"]["content"]["rows"][0]["buttons"][0]
        self.assertFalse(button["action"]["enter"])
        self.assertEqual(button["action"]["type"], 2)
        self.assertIn("tap=453/?/?/0/1", button["action"]["data"])
        self.assertIn(chr(96) * 3, payload["markdown"]["content"])
        self.event.stop_event.assert_called_once()

    async def test_qq_c2c_and_markdown_permission_fallback(self):
        import botpy.message

        source = Mock(spec=botpy.message.C2CMessage)
        source.author = SimpleNamespace(user_openid="test-user")
        self.event.get_platform_name = lambda: "qq_official"
        self.event.message_obj = SimpleNamespace(
            raw_message=source, message_id="test-message"
        )
        sender = AsyncMock(return_value={"id": "reply-id"})
        self.event.bot = SimpleNamespace(api=SimpleNamespace(post_c2c_message=sender))
        self.event.stop_event = Mock()
        self.assertIsNone(
            await score_completion_reply(self.event, self.score, "请补全")
        )
        self.assertEqual(sender.call_args.kwargs["openid"], "test-user")
        sender.side_effect = RuntimeError("Markdown permission unavailable")
        result = await score_completion_reply(self.event, self.score, "请补全")
        self.assertIn("mai识分 id=11167", result.chain[0].text)
        self.assertFalse(result.use_markdown_)


if __name__ == "__main__":
    unittest.main()
