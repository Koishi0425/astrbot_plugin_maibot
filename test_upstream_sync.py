import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_maibot.libraries.maimaidx_alias_stream import iter_sse
from astrbot_plugin_maibot.libraries.maimaidx_api_data import MaimaiAPI
from astrbot_plugin_maibot.libraries.maimaidx_model import (
    Alias,
    AliasStatus,
    PushAliasStatus,
)


class UpstreamSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = MaimaiAPI()
        self.alias = {
            "song_id": 11167,
            "name": "BIRTH",
            "is_votable": True,
            "alias": ["birth", "test"],
        }
        self.vote = {
            "song_id": 11167,
            "name": "BIRTH",
            "tag": "test-tag",
            "apply_alias": "test",
            "created_at": "2026-10-12",
            "agree_votes": 1,
            "votes": 5,
        }

    def test_dx_stars_include_exact_thresholds(self):
        from astrbot_plugin_maibot.libraries.maimai_best_50 import dxScore

        for stars, threshold in enumerate((85, 90, 93, 95, 97), 1):
            with self.subTest(threshold=threshold):
                self.assertEqual(dxScore(threshold), stars)
                self.assertEqual(dxScore(threshold - 0.001), stars - 1)
        self.assertEqual(dxScore(100), 5)

    def test_alias_models_read_new_api_and_existing_cache(self):
        parsed = Alias.model_validate(self.alias)
        self.assertEqual(parsed.SongID, 11167)
        self.assertEqual(parsed.Alias, ["birth", "test"])
        self.assertEqual(Alias.model_validate(parsed.model_dump()), parsed)
        self.assertEqual(AliasStatus.model_validate(self.vote).Time, "2026-10-12")
        push = PushAliasStatus.model_validate(
            {"type": "Apply", "status": [self.vote, dict(self.vote, song_id=11661)]}
        )
        self.assertEqual([item.SongID for item in push.Status], [11167, 11661])

    async def test_alias_v2_response_is_normalized_for_local_cache(self):
        self.api._requestalias = AsyncMock(return_value=[self.alias])
        self.assertEqual(
            await self.api.get_alias(),
            [{"SongID": 11167, "Name": "BIRTH", "Alias": ["birth", "test"]}],
        )
        self.api._requestalias.assert_awaited_once_with(
            "GET", "/aliases/maimaidx/aliases"
        )

    async def test_plate_catalog_uses_v2_endpoint(self):
        self.api._requestalias = AsyncMock(return_value={"maimai": [1]})
        self.assertEqual(await self.api.get_plate_json(), {"maimai": [1]})
        self.api._requestalias.assert_awaited_once_with(
            "GET", "/maimaidx/music/get_plate"
        )

    async def test_search_can_return_ongoing_votes(self):
        self.api._requestalias = AsyncMock(
            return_value={"type": "ongoing", "data": [self.vote]}
        )
        result = await self.api.get_songs("test")
        self.assertIsInstance(result[0], AliasStatus)
        self.assertEqual(result[0].Tag, "test-tag")

    async def test_search_can_return_aliases_or_not_found(self):
        self.api._requestalias = AsyncMock(
            return_value={"type": "approved", "data": [self.alias]}
        )
        self.assertIsInstance((await self.api.get_songs("birth"))[0], Alias)
        self.api._requestalias.return_value = {"message": "not found"}
        self.assertEqual(await self.api.get_songs("missing"), [])

    async def test_votes_and_applications_use_v2_contract_offline(self):
        self.api._requestalias = AsyncMock(return_value={"message": "submitted"})
        self.assertEqual(
            await self.api.post_alias(11167, "test", 123456789, 987654321), "submitted"
        )
        self.api._requestalias.assert_awaited_with(
            "POST",
            "/aliases/maimaidx/apply",
            json={
                "song_id": 11167,
                "apply_alias": "test",
                "apply_uid": 123456789,
                "group_id": 987654321,
            },
        )
        self.assertEqual(
            await self.api.post_agree_user("test-tag", 123456789), "submitted"
        )
        self.api._requestalias.assert_awaited_with(
            "POST",
            "/aliases/maimaidx/votes",
            json={"tag": "test-tag", "agree_user": 123456789},
        )

    async def test_alias_merge_preserves_order_and_deduplicates_local_aliases(self):
        from astrbot_plugin_maibot.libraries import maimaidx_music as music

        with (
            patch.object(
                music, "local_alias_file", SimpleNamespace(exists=lambda: True)
            ),
            patch.object(
                music,
                "read_json",
                return_value={"11167": ["test", "local"]},
            ),
            patch.object(music, "writefile", new=AsyncMock()),
            patch.object(
                music.maiApi,
                "get_alias",
                new=AsyncMock(
                    return_value=[
                        {
                            "SongID": 11167,
                            "Name": "BIRTH",
                            "Alias": ["birth", "test", "birth"],
                        }
                    ]
                ),
            ),
            patch.object(
                music.mai,
                "total_list",
                [SimpleNamespace(id="11167", title="BIRTH")],
                create=True,
            ),
        ):
            result = await music.get_music_alias_list()
        self.assertEqual(result[0].Alias, ["birth", "test", "local"])

    async def test_sse_handles_utf8_bom_heartbeat_multiline_and_resume_id(self):
        async def lines():
            for line in (
                b"\xef\xbb\xbf: heartbeat\r\n",
                "id: 123\n",
                "retry: 5000\n",
                "event: alias\n",
                'data: {"type":"Apply",\n',
                'data: "status":[]}\n',
                "\n",
                ": keepalive\n",
                "\n",
                "event: alias\n",
                "data: next\n",
                "\n",
            ):
                yield line

        result = [event async for event in iter_sse(lines())]
        events = [event for event in result if event["event"] == "alias"]
        self.assertEqual(
            events[0],
            {
                "event": "alias",
                "data": '{"type":"Apply",\n"status":[]}',
                "id": "123",
                "retry": 5000,
            },
        )
        self.assertEqual(events[1]["id"], "123")

    async def test_sse_does_not_dispatch_incomplete_events_after_disconnect(self):
        async def lines():
            for line in ("event: alias", "data: incomplete"):
                yield line

        self.assertEqual([event async for event in iter_sse(lines())], [])

    async def test_sse_rejects_nul_ids_and_invalid_retry_values(self):
        async def lines():
            for line in ("id: bad\0id", "retry: nope", "data: valid", ""):
                yield line

        result = [event async for event in iter_sse(lines())]
        self.assertIsNone(result[0]["id"])
        self.assertIsNone(result[0]["retry"])

    async def test_sse_empty_id_resets_resume_identity(self):
        async def lines():
            for line in ("id: 123", "data: first", "", "id:", "data: second", ""):
                yield line

        result = [event async for event in iter_sse(lines())]
        self.assertEqual([event["id"] for event in result], ["123", ""])


if __name__ == "__main__":
    unittest.main()
