import unittest
from pathlib import Path

from astrbot_plugin_maibot.libraries.maimaidx_advanced import (
    build_diving_fish_records,
    chart_distribution,
    course_catalog,
    is_ap,
    sorted_records,
)


class AdvancedQueryTests(unittest.TestCase):
    def test_build_diving_fish_records(self):
        songs = {
            11451: {"title": "Test Song", "type": "dx"},
        }
        payload, skipped = build_diving_fish_records(
            [
                {
                    "musicId": 11451,
                    "level": 3,
                    "achievement": 1004200,
                    "deluxscoreMax": 2711,
                    "comboStatus": 4,
                    "syncStatus": 5,
                },
                {"musicId": 11451, "level": 10, "achievement": 1000000},
                {"musicId": 99999, "level": 3, "achievement": 1000000},
            ],
            songs.get,
        )
        self.assertEqual(skipped, 2)
        self.assertEqual(payload, [{
            "achievements": 100.42,
            "dxScore": 2711,
            "fc": "app",
            "fs": "fsdp",
            "level_index": 3,
            "title": "Test Song",
            "type": "DX",
        }])

    def test_ap_and_top_sort(self):
        records = [
            {"musicId": 1, "level": 3, "fc": "ap", "ra": 280},
            {"musicId": 2, "level": 3, "fc": "fc", "ra": 300},
            {"musicId": 3, "level": 4, "comboStatus": 4, "ra": 290},
        ]
        selected = sorted_records(records, limit=50, predicate=is_ap)
        self.assertEqual([record["musicId"] for record in selected], [3, 1])

    def test_distribution_rates(self):
        rates = chart_distribution({"fc_dist": [50, 20, 10, 15, 5]})
        self.assertAlmostEqual(rates["fc_rate"], 50)
        self.assertAlmostEqual(rates["ap_rate"], 20)

    def test_prism_plus_courses_are_complete(self):
        path = Path(__file__).parent / "static" / "dan_courses_cn_prism_plus.json"
        catalog = course_catalog(path)
        self.assertEqual(len(catalog["courses"]), 22)
        self.assertTrue(all(len(tracks) == 4 for tracks in catalog["courses"].values()))


if __name__ == "__main__":
    unittest.main()
