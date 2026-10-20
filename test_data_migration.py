"""Verify upgrades preserve user settings and use AstrBot's data directory."""

import tempfile
import asyncio
import shutil
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
        self.temporary_root = Path(temporary.name)
        self.root = self.temporary_root / "plugin"
        self.root.mkdir()
        self.static = self.root / "static"
        self.static.mkdir()
        self.data = self.temporary_root / "persistent"
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
        self.assertEqual(self.namespace["resource_dir"], self.data / "static")

    def test_upgrade_replaces_code_but_preserves_resources_and_user_data(self):
        for relative in (
            "mai/cover/1.png",
            "mai/pic/prism_plus/b50.png",
            "font/ResourceHanRoundedCN-Bold.ttf",
        ):
            path = self.static / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"asset")
        for name in ("user_qq_bindings.json", "disabled_groups.json", "arcades.json"):
            (self.static / name).write_text('{"keep": true}', "utf-8")
        with patch.object(StarTools, "get_data_dir", return_value=self.data):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)
        # Simulate the framework replacing exactly this temporary plugin checkout.
        self.assertTrue(
            self.root.resolve().is_relative_to(self.temporary_root.resolve())
        )
        shutil.rmtree(self.root)
        self.static.mkdir(parents=True)
        (self.static / "echarts.min.js").write_bytes(b"new bundled script")
        with patch.object(StarTools, "get_data_dir", return_value=self.data):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)
        self.assertEqual((self.namespace["coverdir"] / "1.png").read_bytes(), b"asset")
        self.assertEqual(self.namespace["SIYUAN"].read_bytes(), b"asset")
        self.assertEqual(
            (self.namespace["static"] / "echarts.min.js").read_bytes(),
            b"new bundled script",
        )
        for name in ("user_qq_bindings.json", "disabled_groups.json", "arcades.json"):
            self.assertEqual((self.data / name).read_text("utf-8"), '{"keep": true}')

    def test_asset_migration_never_overwrites_persistent_resource_updates(self):
        old = self.static / "mai/cover/1.png"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old")
        current = self.data / "static/mai/cover/1.png"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"current")
        with patch.object(StarTools, "get_data_dir", return_value=self.data):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)
        self.assertEqual(current.read_bytes(), b"current")

    def test_interrupted_migration_does_not_publish_partial_json(self):
        (self.static / "user_qq_bindings.json").write_text('{"keep": true}', "utf-8")

        def incomplete_copy(source, target):
            Path(target).write_bytes(b'{"partial":')
            raise OSError("disk full")

        with (
            patch.object(StarTools, "get_data_dir", return_value=self.data),
            patch("shutil.copy2", side_effect=incomplete_copy),
            self.assertRaises(OSError),
        ):
            exec(compile(self.source, "__init__.py", "exec"), self.namespace)
        self.assertFalse((self.data / "user_qq_bindings.json").exists())
        self.assertEqual(list(self.data.iterdir()), [])

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
            documents = []

            async def capture(target):
                documents.append((target, target.read_text("utf-8")))
                return "base64://test"

            with (
                patch.object(score, "get_astrbot_temp_path", return_value=temporary),
                patch.object(
                    score,
                    "run_chrome_to_base64",
                    capture,
                ),
            ):
                await score.music_global_data(music, 0)
            self.assertIn(
                score.static.resolve().as_uri() + "/echarts.min.js",
                documents[0][1],
            )
            self.assertFalse(documents[0][0].exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])

    async def test_concurrent_queries_capture_their_own_song_and_clean_up(self):
        from astrbot_plugin_maibot.libraries import maimaidx_player_score as score

        paths = []
        ready = asyncio.Event()

        async def capture(target):
            paths.append(target)
            if len(paths) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 2)
            text = target.read_text("utf-8")
            return "base64://song-A" if "song-A" in text else "base64://song-B"

        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(score, "get_astrbot_temp_path", return_value=temporary),
                patch.object(score, "run_chrome_to_base64", capture),
            ):
                results = await asyncio.gather(
                    *(
                        score.music_global_data(
                            SimpleNamespace(
                                id=str(index),
                                title=title,
                                stats=[
                                    SimpleNamespace(
                                        fc_dist=[index] * 5, dist=[index] * 14
                                    )
                                ],
                            ),
                            0,
                        )
                        for index, title in enumerate(("song-A", "song-B"), 1)
                    )
                )
            self.assertEqual(
                [result.data["file"] for result in results],
                ["base64://song-A", "base64://song-B"],
            )
            self.assertEqual(len(set(paths)), 2)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    async def test_chart_capture_failure_cleans_temporary_files(self):
        from astrbot_plugin_maibot.libraries import maimaidx_player_score as score

        music = SimpleNamespace(
            id="1",
            title="Test",
            stats=[SimpleNamespace(fc_dist=[1] * 5, dist=[1] * 14)],
        )
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(score, "get_astrbot_temp_path", return_value=temporary),
                patch.object(
                    score, "run_chrome_to_base64", AsyncMock(side_effect=OSError)
                ),
                self.assertRaises(OSError),
            ):
                await score.music_global_data(music, 0)
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
