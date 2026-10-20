"""Keep local arcade subscriptions and queues across reloads and catalog updates."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_plugin_maibot.libraries import maimaidx_arcade as module
from astrbot_plugin_maibot.libraries.maimaidx_storage import CorruptDataError


class ArcadePersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.file = Path(temporary.name) / "arcades.json"
        self.saved = {
            "name": "Local arcade",
            "location": "Old address",
            "province": "P",
            "mall": "M",
            "num": 2,
            "id": "1",
            "alias": ["my alias"],
            "group": [123],
            "person": 7,
            "by": "player",
            "time": "saved time",
        }
        self.file.write_text(json.dumps([self.saved]), "utf-8")
        patcher = patch.object(module, "arcades_json", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = module.ArcadeData()
        patcher = patch.object(module, "arcade", self.owner)
        patcher.start()
        self.addCleanup(patcher.stop)
        response = Mock(status=200)
        response.json = AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "arcadeName": "Official name",
                    "address": "New address",
                    "province": "P",
                    "mall": "M",
                    "machineCount": 3,
                }
            ]
        )
        self.request = Mock()
        self.request.__aenter__ = AsyncMock(return_value=response)
        self.request.__aexit__ = AsyncMock(return_value=False)
        patcher = patch.object(module.aiohttp, "request", return_value=self.request)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_numeric_remote_ids_keep_existing_string_id_settings(self):
        await self.owner.getArcade()
        item = self.owner.total[0]
        self.assertEqual(
            (item.alias, item.group, item.person), (["my alias"], [123], 7)
        )
        self.assertEqual((item.name, item.location), ("Official name", "New address"))
        self.assertEqual(json.loads(self.file.read_text("utf-8"))[0]["person"], 7)

    async def test_refresh_reads_latest_saved_queue_instead_of_startup_snapshot(self):
        latest = dict(self.saved, person=9, group=[123, 456], alias=["new alias"])
        self.file.write_text(json.dumps([latest]), "utf-8")
        await self.owner.getArcade()
        self.assertEqual(self.owner.total[0].person, 9)
        self.assertEqual(self.owner.total[0].group, [123, 456])
        self.assertEqual(self.owner.total[0].alias, ["new alias"])

    async def test_save_and_restart_keep_queue_and_subscriptions(self):
        self.owner.total = module.ArcadeList([module.Arcade(**self.saved)])
        self.owner.total[0].person = 11
        await self.owner.total.save_arcade()
        restarted = module.ArcadeData()
        with patch.object(module, "arcade", restarted):
            await restarted.getArcade()
        self.assertEqual(restarted.total[0].person, 11)
        self.assertEqual(restarted.total[0].group, [123])
        self.assertEqual(self.owner.arcades[0]["person"], 11)

    async def test_temporarily_missing_remote_arcade_keeps_local_data(self):
        self.request.__aenter__.return_value.json.return_value = []
        await self.owner.getArcade()
        self.assertEqual(self.owner.total[0].person, 7)
        self.assertEqual(self.owner.total[0].group, [123])

    async def test_saving_custom_arcades_refreshes_ids_for_the_next_addition(self):
        self.owner.total.add_arcade(dict(self.saved, id="10000", name="Custom"))
        await self.owner.total.save_arcade()
        self.assertEqual(self.owner.idList, [1, 10000])
        await self.owner.getArcade()
        self.assertEqual(self.owner.total.search_id("10000")[0].person, 7)

    async def test_corrupt_store_is_preserved_and_never_replaced_by_remote_data(self):
        self.file.write_bytes(b'{"broken":')
        with self.assertRaises(CorruptDataError):
            await self.owner.getArcade()
        self.assertEqual(self.file.read_bytes(), b'{"broken":')
        self.assertTrue(list(self.file.parent.glob("*.corrupt-*.bak")))

    async def test_invalid_entry_schema_preserves_backup_before_refresh_fails(self):
        original = b'[{"id":"1"}]'
        self.file.write_bytes(original)
        with self.assertRaises(ValueError):
            await self.owner.getArcade()
        self.assertEqual(self.file.read_bytes(), original)
        backup = next(self.file.parent.glob("*.corrupt-*.bak"))
        self.assertEqual(backup.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
