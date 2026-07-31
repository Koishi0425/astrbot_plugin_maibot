import unittest

from astrbot_plugin_maibot.libraries.maimaidx_music_notes import (
    build_music_info_note_columns,
)


class MusicInfoNoteColumnTests(unittest.TestCase):
    def test_dx_chart_includes_total_before_each_note_type(self):
        self.assertEqual(
            build_music_info_note_columns([100, 20, 30, 40, 5]),
            [195, 100, 20, 30, 40, 5],
        )

    def test_standard_chart_uses_placeholder_for_touch(self):
        self.assertEqual(
            build_music_info_note_columns([100, 20, 30, 5]),
            [155, 100, 20, 30, "-", 5],
        )


if __name__ == "__main__":
    unittest.main()
