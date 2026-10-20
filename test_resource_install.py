"""Offline regressions for full archive detection and resource update reporting."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import py7zr

from astrbot_plugin_maibot.command import mai_update as command
from astrbot_plugin_maibot.libraries import maimaidx_resource as resource
from astrbot_plugin_maibot.libraries.maimaidx_api_data import MaiConfig


class ResourceInstallerTests(unittest.TestCase):
    def setUp(self):
        """Isolate source trees and installation targets."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        target = self.root / "installed"
        for name, path in {
            "resource_dir": target,
            "maimaidir": target / "mai/pic",
            "ratingdir": self.root / "data/rating_table",
            "platedir": self.root / "data/plate_table",
            "plate_versiondir": target / "mai/plate_version",
            "shougoudir": target / "mai/shougou",
        }.items():
            patcher = patch.object(resource, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_wrapped_full_archive_copies_theme_fonts_covers_and_digits(self):
        files = (
            "mai/pic/prism_plus/b50.png",
            "mai/pic/prism_plus/title.png",
            "mai/pic/prism_plus/title_lengthen.png",
            "mai/pic/b50_score_basic.png",
            "mai/cover/0.png",
            "font/ResourceHanRoundedCN-Bold.ttf",
        ) + tuple("mai/pic/" + name for name in resource.RATING_DIGIT_NAMES)
        source_static = self.source / "Resource CN1.55/static"
        for relative in files:
            path = source_static / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test asset")
        (source_static / "config.json").write_text('{"secret": "excluded"}', "utf-8")
        (source_static / "user_qq_bindings.json").write_text('{"old": true}', "utf-8")
        archive = self.root / "Resource.7z"
        with py7zr.SevenZipFile(archive, "w") as output:
            output.writeall(self.source / "Resource CN1.55", arcname="Resource CN1.55")
        result = resource.ResourceInstallResult()
        resource._install_archive(archive, result)
        self.assertEqual(result.copied_files, len(files))
        for relative in files:
            self.assertEqual(
                (resource.resource_dir / relative).read_bytes(), b"test asset"
            )
        self.assertFalse((resource.resource_dir / "config.json").exists())
        self.assertIn("config.json", result.skipped_files)
        self.assertIn("user_qq_bindings.json", result.skipped_files)
        self.assertFalse((resource.resource_dir / "user_qq_bindings.json").exists())

    def test_deeply_wrapped_full_static_is_not_treated_as_incremental(self):
        source_static = self.source / "outer/inner/static"
        for name in ("b50.png", "title.png", "title_lengthen.png"):
            path = source_static / "mai/pic/prism_plus" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"theme")
        (source_static / "mai/pic" / resource.RATING_DIGIT_NAMES[0]).write_bytes(
            b"digit"
        )
        result = resource.ResourceInstallResult()
        resource._install_resource_dir(self.source, result)
        self.assertEqual(result.copied_files, 4)
        self.assertTrue(
            (resource.resource_dir / "mai/pic/prism_plus/b50.png").is_file()
        )

    def test_digit_only_archive_remains_supported(self):
        source = self.source / "Rating Update"
        source.mkdir()
        for name in resource.RATING_DIGIT_NAMES:
            (source / name).write_bytes(b"digit")
        result = resource.ResourceInstallResult()
        resource._install_resource_dir(self.source, result)
        self.assertEqual(result.copied_files, 10)
        self.assertFalse((resource.resource_dir / "mai/pic/prism_plus").exists())

    def test_unsupported_full_tree_does_not_silently_install_just_digits(self):
        pic = self.source / "old/static/mai/pic"
        pic.mkdir(parents=True)
        (pic / resource.RATING_DIGIT_NAMES[0]).write_bytes(b"digit")
        result = resource.ResourceInstallResult()
        with self.assertRaises(resource.ResourceInstallError):
            resource._install_resource_dir(self.source, result)
        self.assertEqual(result.copied_files, 0)


class ResourceUpdateMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_resources_are_not_reported_as_complete_or_reloaded(self):
        result = resource.ResourceInstallResult(
            copied_files=10, missing_paths=["mai/pic/prism_plus/b50.png"]
        )
        config = MaiConfig(resource_local_path="/data/Resource.7z")
        with (
            patch.object(
                command, "install_maimai_resources", AsyncMock(return_value=result)
            ),
            patch.object(command, "refresh_resource_cache", Mock()) as refresh,
        ):
            messages = await command._update_static_resources(config, True)
        self.assertEqual(messages[0], "静态资源安装未完成。")
        self.assertIn("mai/pic/prism_plus/b50.png", "\n".join(messages))
        refresh.assert_not_called()
        self.assertNotIn("请重载插件或重启 AstrBot", "\n".join(messages))

    async def test_outer_summary_does_not_claim_partial_install_succeeded(self):
        event = Mock()
        event.plain_result = lambda text: text
        config = MaiConfig(resource_local_path="/data/Resource.7z")
        with (
            patch.object(command, "is_superuser", return_value=True),
            patch.object(
                command, "_update_runtime_data", AsyncMock(return_value="数据已更新")
            ),
            patch.object(
                command,
                "_update_static_resources",
                AsyncMock(return_value=["静态资源安装未完成。", "仍缺少素材"]),
            ),
        ):
            messages = [
                item
                async for item in command.update_maimai_all_handler(
                    event, config=config, require_resource_source=True
                )
            ]
        self.assertTrue(messages[-1].startswith("舞萌数据更新完成，静态资源未完成。"))

    async def test_complete_resources_refresh_the_cache(self):
        result = resource.ResourceInstallResult(copied_files=3000)
        with (
            patch.object(
                command, "install_maimai_resources", AsyncMock(return_value=result)
            ),
            patch.object(command, "refresh_resource_cache", Mock()) as refresh,
        ):
            messages = await command._update_static_resources(
                MaiConfig(resource_local_path="/data/Resource.7z"), True
            )
        self.assertEqual(messages[0], "静态资源更新完成。")
        refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
