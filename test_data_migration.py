"""Verify upgrades preserve user settings and use AstrBot's data directory."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api import logger
from astrbot.api.star import StarTools


class DataMigrationTests(unittest.TestCase):
    def setUp(self):
        """Prepare isolated legacy and current plugin directories."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.static = self.root / "static"
        self.static.mkdir()
        self.data = self.root / "data"
        self.data.mkdir()
        self.source = Path(__file__).with_name("__init__.py").read_text("utf-8")
        self.namespace = {"__file__": str(self.root / "__init__.py")}

    def test_migrates_bindings_group_settings_and_caches(self):
        for name in (
            "user_qq_bindings.json",
            "disabled_groups.json",
            "music_data.json",
        ):
            (self.static / name).write_text('{"preserved": true}', "utf-8")
        with patch.object(StarTools, "get_data_dir", return_value=self.data):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)
        for name in (
            "user_qq_bindings.json",
            "disabled_groups.json",
            "music_data.json",
        ):
            self.assertEqual(
                (self.data / name).read_text("utf-8"), '{"preserved": true}'
            )
            self.assertTrue((self.static / name).exists())
        for name in ("user_qq_bindings_file", "pie_html_file", "ratingdir", "platedir"):
            self.assertEqual(self.namespace[name].parent, self.data)
        self.assertIs(self.namespace["log"], logger)
        self.assertEqual(self.namespace["static"], self.static)

    def test_keeps_current_data_and_does_not_copy_legacy_credentials(self):
        (self.static / "user_qq_bindings.json").write_text("old", "utf-8")
        (self.data / "user_qq_bindings.json").write_text("current", "utf-8")
        (self.static / "config.json").write_text("legacy credentials", "utf-8")
        with patch.object(StarTools, "get_data_dir", return_value=self.data):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)
        self.assertEqual(
            (self.data / "user_qq_bindings.json").read_text("utf-8"), "current"
        )
        self.assertFalse((self.data / "config.json").exists())

    def test_copy_failure_prevents_loading_empty_replacement_settings(self):
        (self.static / "disabled_groups.json").write_text("[]", "utf-8")
        with (
            patch.object(StarTools, "get_data_dir", return_value=self.data),
            patch("shutil.copy2", side_effect=OSError("read-only directory")),
            self.assertRaises(OSError),
        ):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)


class ChartPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_chart_loads_static_javascript_from_new_data_path(self):
        from astrbot_plugin_maibot.libraries import maimaidx_player_score as score

        music = SimpleNamespace(
            id="1",
            title="Test",
            stats=[SimpleNamespace(fc_dist=[1] * 5, dist=[1] * 14)],
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "chart.html"
            with (
                patch.object(score, "pie_html_file", target),
                patch.object(
                    score,
                    "run_chrome_to_base64",
                    AsyncMock(return_value="base64://test"),
                ),
            ):
                await score.music_global_data(music, 0)
            self.assertIn(
                score.static.resolve().as_uri() + "/echarts.min.js",
                target.read_text("utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
