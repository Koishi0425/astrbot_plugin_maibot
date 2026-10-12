"""Offline regressions for startup recovery, catalog caches and durable settings."""

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientConnectionError, ClientResponseError
from astrbot_plugin_maibot.libraries import maimaidx_music as music
from astrbot_plugin_maibot.libraries import maimaidx_storage as storage
from astrbot_plugin_maibot.libraries.maimaidx_model import (
    AliasesPush,
    GuessSwitch,
    Music,
)
from test_score_image import MUSIC

BIRTH = Music.model_validate(
    {
        "id": MUSIC.id,
        "title": MUSIC.title,
        "type": MUSIC.type,
        "ds": MUSIC.ds,
        "level": MUSIC.level,
        "cids": [1, 2, 3, 4],
        "charts": [{"notes": chart.notes, "charter": "test"} for chart in MUSIC.charts],
        "basic_info": {
            "title": MUSIC.title,
            "artist": MUSIC.basic_info.artist,
            "genre": "test",
            "bpm": 176,
            "from": MUSIC.basic_info.version,
            "is_new": False,
        },
    }
)


class StorageTests(unittest.TestCase):
    def setUp(self):
        """Prepare an isolated store."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / "settings.json"

    def test_atomic_replace_failure_preserves_old_content_and_cleans_temp(self):
        storage.write_json(self.file, {"keep": "old"})
        original = self.file.read_bytes()
        with patch.object(storage.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                storage.write_json(self.file, {"keep": "new"})
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [self.file])

    def test_complete_atomic_replacement_and_non_serializable_data(self):
        storage.write_json(self.file, {"group": ["中文"]})
        self.assertEqual(storage.read_json(self.file), {"group": ["中文"]})
        with self.assertRaises(ValueError):
            storage.write_json(self.file, {"bad": float("nan")})
        self.assertEqual(storage.read_json(self.file), {"group": ["中文"]})

    def test_corrupt_store_keeps_original_and_deduplicated_backup(self):
        original = b'{"old_user":'
        self.file.write_bytes(original)
        for _ in range(2):
            with self.assertRaises(storage.CorruptDataError):
                storage.write_json(self.file, {})
        backups = list(self.root.glob("*.corrupt-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.file.read_bytes(), original)

    def test_wrong_root_is_preserved_and_public_cache_can_be_rebuilt(self):
        self.file.write_text("[]", "utf-8")
        with self.assertRaises(storage.CorruptDataError):
            storage.read_json(self.file, dict)
        storage.write_json(self.file, {"fresh": True}, protect_corrupt=False)
        self.assertEqual(storage.read_json(self.file), {"fresh": True})
        self.assertEqual(next(self.root.glob("*.bak")).read_text("utf-8"), "[]")


class CacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Use real model-valid song data and temporary cache paths."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("music_file", "chart_file", "alias_file", "local_alias_file"):
            patcher = patch.object(music, name, self.root / f"{name}.json")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(music, "data_dir", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.catalog = [BIRTH.model_dump(by_alias=True)]
        storage.write_json(music.music_file, self.catalog)
        storage.write_json(music.chart_file, {"charts": {BIRTH.id: [{"cnt": 18000}]}})
        self.owner = music.MaiMusic()
        self.owner.total_list = music.MusicList([BIRTH])
        patcher = patch.object(music, "mai", self.owner)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_connection_and_http_failures_use_cached_catalogs(self):
        for failure in (
            ClientConnectionError("offline"),
            ClientResponseError(None, (), status=503),
        ):
            with (
                patch.object(
                    music.maiApi, "music_data", AsyncMock(side_effect=failure)
                ),
                patch.object(
                    music.maiApi, "chart_stats", AsyncMock(side_effect=failure)
                ),
            ):
                songs = await music.get_music_list()
            self.assertEqual(songs[0].id, BIRTH.id)
            self.assertEqual(songs[0].stats[0].cnt, 18000)

    async def test_invalid_server_response_does_not_replace_good_cache(self):
        original = music.music_file.read_bytes()
        with (
            patch.object(
                music.maiApi, "music_data", AsyncMock(return_value=[{"id": "bad"}])
            ),
            patch.object(
                music.maiApi, "chart_stats", AsyncMock(return_value={"charts": {}})
            ),
        ):
            songs = await music.get_music_list()
        self.assertEqual(songs[0].title, BIRTH.title)
        self.assertEqual(music.music_file.read_bytes(), original)

    async def test_missing_statistics_still_allows_basic_song_queries(self):
        music.chart_file.unlink()
        with (
            patch.object(
                music.maiApi, "music_data", AsyncMock(side_effect=ClientConnectionError)
            ),
            patch.object(
                music.maiApi,
                "chart_stats",
                AsyncMock(side_effect=ClientConnectionError),
            ),
        ):
            songs = await music.get_music_list()
        self.assertEqual(songs[0].charts[3].notes.tap, 697)
        self.assertEqual(songs[0].stats, [])

    async def test_cache_write_failure_keeps_fresh_data_usable(self):
        with (
            patch.object(
                music.maiApi, "music_data", AsyncMock(return_value=self.catalog)
            ),
            patch.object(
                music.maiApi, "chart_stats", AsyncMock(return_value={"charts": {}})
            ),
            patch.object(
                music, "writefile", AsyncMock(side_effect=OSError("read only"))
            ),
        ):
            songs = await music.get_music_list()
        self.assertEqual(songs[0].id, BIRTH.id)

    async def test_alias_failure_uses_cache_and_local_aliases(self):
        storage.write_json(
            music.alias_file,
            [{"SongID": int(BIRTH.id), "Name": BIRTH.title, "Alias": ["cached"]}],
        )
        storage.write_json(music.local_alias_file, {BIRTH.id: ["local"]})
        with patch.object(
            music.maiApi, "get_alias", AsyncMock(side_effect=ClientConnectionError)
        ):
            aliases = await music.get_music_alias_list()
        self.assertEqual(aliases[0].Alias, ["cached", "local"])

    async def test_alias_failure_without_cache_uses_titles(self):
        with patch.object(
            music.maiApi, "get_alias", AsyncMock(side_effect=ClientConnectionError)
        ):
            aliases = await music.get_music_alias_list()
        self.assertIn(BIRTH.title.lower(), aliases[0].Alias)

    async def test_plate_cache_survives_connection_failure(self):
        storage.write_json(self.root / "music_plate.json", {"prism": [11167]})
        with patch.object(
            music.maiApi, "get_plate_json", AsyncMock(side_effect=ClientConnectionError)
        ):
            await self.owner.get_plate_json()
        self.assertEqual(self.owner.total_plate_id_list, {"prism": [11167]})

    async def test_guess_refresh_does_not_accumulate_duplicate_hot_ids(self):
        with (
            patch.object(
                music.maiApi, "music_data", AsyncMock(side_effect=ClientConnectionError)
            ),
            patch.object(
                music.maiApi,
                "chart_stats",
                AsyncMock(side_effect=ClientConnectionError),
            ),
        ):
            self.owner.total_list = await music.get_music_list()
        self.owner.guess()
        self.owner.guess()
        self.assertEqual(self.owner.hot_music_ids, [BIRTH.id])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        """Extract initialization methods without constructing a running platform."""
        source = ast.parse(Path(__file__).with_name("main.py").read_text("utf-8"))
        plugin = next(
            node
            for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "MaimaiDXPlugin"
        )
        names = {
            "initialize",
            "_load_maimai_data",
            "_load_disabled_groups",
            "_save_disabled_groups",
            "_is_group_enabled",
            "toggle_maimai_feature",
        }
        methods = []
        for node in plugin.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names
            ):
                node.decorator_list = []
                methods.append(node)
        extracted = ast.ClassDef(
            name="Plugin", bases=[], keywords=[], body=methods, decorator_list=[]
        )
        cls.namespace = dict(
            log=Mock(),
            maiApi=SimpleNamespace(config=SimpleNamespace(arcade_enabled=False)),
            mai=None,
            read_json=storage.read_json,
            write_json=storage.write_json,
            preserve_corrupt=storage.preserve_corrupt,
            AstrMessageEvent=object,
        )
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(body=[extracted], type_ignores=[])
                ),
                "main.py",
                "exec",
            ),
            cls.namespace,
        )
        cls.Plugin = cls.namespace["Plugin"]

    def setUp(self):
        """Prepare independent initialization stages."""
        self.plugin = self.Plugin()
        self.plugin._load_disabled_groups = Mock()
        self.plugin._setup_configuration = Mock()
        self.plugin._load_maimai_data = AsyncMock()
        self.plugin._load_arcade_data = AsyncMock()
        self.plugin._load_images_to_memory = Mock(
            side_effect=FileNotFoundError("missing")
        )
        self.plugin._perform_initial_checks = Mock()
        self.plugin._setup_schedulers = Mock()
        for name in (
            "_load_disabled_groups",
            "_setup_configuration",
            "_load_images_to_memory",
            "_perform_initial_checks",
            "_setup_schedulers",
        ):
            getattr(self.plugin, name).__name__ = name

    async def test_missing_images_still_runs_resource_checks_and_schedules(self):
        await self.plugin.initialize()
        self.plugin._perform_initial_checks.assert_called_once()
        self.plugin._setup_schedulers.assert_called_once()

    async def test_offline_initialization_still_registers_schedules(self):
        self.plugin._load_maimai_data.side_effect = ClientConnectionError("offline")
        self.plugin._perform_initial_checks.side_effect = OSError("missing directory")
        await self.plugin.initialize()
        self.plugin._setup_schedulers.assert_called_once()

    async def test_plate_failure_does_not_skip_aliases_or_guessing(self):
        owner = SimpleNamespace(
            get_music=AsyncMock(),
            get_plate_json=AsyncMock(side_effect=ClientConnectionError),
            get_music_alias=AsyncMock(),
            guess=Mock(),
        )
        self.namespace["mai"] = owner
        for name in ("get_music", "get_plate_json", "get_music_alias"):
            getattr(owner, name).__name__ = name
        await self.Plugin._load_maimai_data(self.plugin)
        owner.get_music_alias.assert_awaited_once()
        owner.guess.assert_called_once()

    async def test_corrupt_group_settings_block_commands_and_remain_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin = self.Plugin()
            plugin.data_file = Path(temporary) / "disabled_groups.json"
            plugin.data_file.write_bytes(b'{"disabled_groups":')
            plugin.disabled_groups = set()
            plugin._load_disabled_groups()
            self.assertFalse(plugin._is_group_enabled("123"))
            self.assertEqual(plugin.data_file.read_bytes(), b'{"disabled_groups":')
            self.assertEqual(len(list(Path(temporary).glob("*.bak"))), 1)

    async def test_group_toggle_save_failure_keeps_current_state(self):
        plugin = self.Plugin()
        plugin.disabled_groups = {"123"}
        plugin.group_settings_available = True
        plugin.superusers = ["admin"]
        plugin._save_disabled_groups = Mock(side_effect=OSError("disk full"))
        event = SimpleNamespace(
            get_sender_id=lambda: "admin",
            message_obj=SimpleNamespace(group_id="123"),
            message_str="开启舞萌功能",
            plain_result=lambda text: text,
        )
        result = [item async for item in plugin.toggle_maimai_feature(event)]
        self.assertIn("保存失败", result[0])
        self.assertEqual(plugin.disabled_groups, {"123"})


class UserStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_and_group_store_corruption_never_resets_users(self):
        from astrbot_plugin_maibot.libraries import maimaidx_identity as identity
        from astrbot_plugin_maibot.libraries import maimaidx_group_ranking as ranking

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "users.json"
            original = b'{"existing_user":'
            path.write_bytes(original)
            event = SimpleNamespace(
                get_sender_id=lambda: "openid", get_platform_name=lambda: "qq_official"
            )
            with patch.object(identity, "user_qq_bindings_file", path):
                with self.assertRaises(storage.CorruptDataError):
                    await identity.bind_event_qq(event, "123456")
                result = await identity.resolve_sender_qq(event)
                self.assertIn("数据损坏", result.error)
            with patch.object(ranking, "group_rating_file", path):
                with self.assertRaises(storage.CorruptDataError):
                    await ranking._load_store()
                with self.assertRaises(storage.CorruptDataError):
                    await ranking._save_store({})
            self.assertEqual(path.read_bytes(), original)

    async def test_settings_write_failure_does_not_change_live_switches(self):
        owner = music.Guess()
        owner.switch = GuessSwitch(enable=["one"], disable=[])
        aliases = music.GroupAlias()
        aliases.push = AliasesPush(enable=[], disable=["one"])
        with patch.object(
            music, "writefile", AsyncMock(side_effect=OSError("disk full"))
        ):
            self.assertIn("保存失败", await owner.off("one"))
            self.assertEqual(owner.switch.enable, ["one"])
            self.assertIn("保存失败", await aliases.on("one"))
            self.assertEqual(aliases.push.disable, ["one"])
            with self.assertRaises(OSError):
                await aliases.alias_global_change(True, ["two"])
            self.assertEqual(aliases.push.disable, ["one"])

    async def test_corrupt_alias_settings_disable_broadcasts_without_import_failure(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "alias.json"
            path.write_bytes(b"invalid")
            with patch.object(music, "group_alias_file", path):
                aliases = music.GroupAlias()
                self.assertFalse(aliases.storage_available)
                self.assertIn("设置损坏", await aliases.on("one"))
            self.assertEqual(path.read_bytes(), b"invalid")


if __name__ == "__main__":
    unittest.main()
