import unittest

from astrbot_plugin_maibot.libraries.maimaidx_music_rating import (
    MUSIC_INFO_RATING_ACHIEVEMENTS,
    MUSIC_INFO_RATING_X_POSITIONS,
    build_music_info_ratings,
)


class MusicInfoRatingTests(unittest.TestCase):
    def test_builds_ap_and_six_standard_ratings_in_display_order(self):
        calls = []

        def calculator(ds, achievement):
            calls.append((ds, achievement))
            return int(ds * 10 + achievement * 2)

        ratings = build_music_info_ratings(13.4, calculator)
        standard = [calculator(13.4, value) for value in MUSIC_INFO_RATING_ACHIEVEMENTS]

        self.assertEqual(ratings, [standard[0] + 1, *standard])
        self.assertEqual(calls[:6], [(13.4, value) for value in MUSIC_INFO_RATING_ACHIEVEMENTS])

    def test_ap_is_exactly_one_more_than_sss_plus(self):
        ratings = build_music_info_ratings(1.0, lambda _ds, achievement: int(achievement))

        self.assertEqual(len(ratings), 7)
        self.assertEqual(ratings[0], ratings[1] + 1)

    def test_seven_columns_extend_the_existing_layout(self):
        self.assertEqual(
            MUSIC_INFO_RATING_X_POSITIONS,
            (295, 420, 545, 670, 795, 920, 1045),
        )


if __name__ == "__main__":
    unittest.main()
