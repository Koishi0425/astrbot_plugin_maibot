import unittest
from pathlib import Path

from astrbot_plugin_maibot.libraries.maimaidx_advanced import (
    chart_distribution,
    course_catalog,
    is_ap,
    sorted_records,
)


class AdvancedQueryTests(unittest.TestCase):
    def test_ap_and_top_sort(self):
        records = [
            {"song_id": 1, "level_index": 3, "fc": "ap", "ra": 280},
            {"song_id": 2, "level_index": 3, "fc": "fc", "ra": 300},
            {"song_id": 3, "level_index": 4, "fc": "app", "ra": 290},
        ]
        selected = sorted_records(records, limit=50, predicate=is_ap)
        self.assertEqual([record["song_id"] for record in selected], [3, 1])

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
