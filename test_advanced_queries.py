import json
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_maibot.libraries.maimaidx_advanced import (
    chart_distribution,
    course_catalog,
    find_latest_sdgb_dump,
    is_ap,
    parse_qq_user_mapping,
    sorted_records,
)


class AdvancedQueryTests(unittest.TestCase):
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

    def test_mapping_parser(self):
        self.assertEqual(parse_qq_user_mapping('{"12345": 678}'), {"12345": 678})
        self.assertEqual(parse_qq_user_mapping(""), {})

    def test_exact_user_dump_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "001"
            right = root / "002"
            wrong.mkdir()
            right.mkdir()
            (wrong / "index.json").write_text(json.dumps({"userId": 1, "files": {"GetUserMusicApi": "GetUserMusicApi.json"}}), encoding="utf-8")
            (wrong / "GetUserMusicApi.json").write_text(json.dumps({"userMusicList": []}), encoding="utf-8")
            (right / "index.json").write_text(json.dumps({"userId": 2, "files": {"GetUserMusicApi": "GetUserMusicApi.json"}}), encoding="utf-8")
            (right / "GetUserMusicApi.json").write_text(json.dumps({"userMusicList": [{"userMusicDetailList": [{"musicId": 11469, "level": 3, "playCount": 7}]}]}), encoding="utf-8")
            dump_dir, records = find_latest_sdgb_dump(root, 2)
            self.assertEqual(dump_dir.name, "002")
            self.assertEqual(records[0]["playCount"], 7)

    def test_prism_plus_courses_are_complete(self):
        path = Path(__file__).parent / "static" / "dan_courses_cn_prism_plus.json"
        catalog = course_catalog(path)
        self.assertEqual(len(catalog["courses"]), 22)
        self.assertTrue(all(len(tracks) == 4 for tracks in catalog["courses"].values()))


if __name__ == "__main__":
    unittest.main()
