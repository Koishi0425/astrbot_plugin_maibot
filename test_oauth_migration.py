import asyncio
import ast
import hashlib
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_maibot.libraries.maimaidx_api_data import MaiConfig, MaimaiAPI
from astrbot_plugin_maibot.libraries.maimaidx_oauth import DivingFishOAuth, OAuthError


class OAuthCommandTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse(
            Path(__file__).with_name("main.py").read_text(encoding="utf-8")
        )
        plugin = next(
            node
            for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "MaimaiDXPlugin"
        )
        handler = next(
            node
            for node in plugin.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "authorize_divingfish"
        )
        handler.decorator_list = []
        cls.api = SimpleNamespace(oauth=SimpleNamespace(authorize=AsyncMock()))
        namespace = {
            "__package__": "astrbot_plugin_maibot",
            "AstrMessageEvent": object,
            "maiApi": cls.api,
        }
        exec(
            compile(ast.Module(body=[handler], type_ignores=[]), "main.py", "exec"),
            namespace,
        )
        cls.handler = namespace["authorize_divingfish"]

    def setUp(self):
        self.plugin = SimpleNamespace(
            _is_group_enabled=lambda group: group != "disabled"
        )
        self.event = SimpleNamespace(
            message_obj=SimpleNamespace(group_id=""), plain_result=lambda text: text
        )
        self.api.oauth.authorize = AsyncMock(
            return_value={
                "binding_label": "QQ 12*****89",
                "user_code": "TEST-CODE",
                "expires_in": 600,
                "verification_uri_complete": "https://auth.diving-fish.com/device?user_code=TEST-CODE",
            }
        )

    async def test_authorization_shows_user_code_and_masked_identity(self):
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_identity.resolve_sender_qq",
            new=AsyncMock(return_value=SimpleNamespace(ok=True, qqid="123456789")),
        ):
            messages = [
                message async for message in type(self).handler(self.plugin, self.event)
            ]
        self.assertIn("TEST-CODE", messages[0])
        self.assertIn("QQ 12*****89", messages[0])
        self.assertIn("请勿转发", messages[0])
        self.assertNotIn("123456789", messages[0])
        self.api.oauth.authorize.assert_awaited_once_with("123456789")

    async def test_missing_identity_and_disabled_groups_do_not_start_authorization(
        self,
    ):
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_identity.resolve_sender_qq",
            new=AsyncMock(return_value=SimpleNamespace(ok=False, error="请绑定QQ")),
        ):
            self.assertEqual(
                [
                    message
                    async for message in type(self).handler(self.plugin, self.event)
                ],
                ["请绑定QQ"],
            )
        self.event.message_obj.group_id = "disabled"
        self.assertEqual(
            [message async for message in type(self).handler(self.plugin, self.event)],
            [],
        )
        self.api.oauth.authorize.assert_not_awaited()


class OAuthMigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.oauth = DivingFishOAuth("operator-app", "test-secret")
        self.subject = self.oauth.subject(qqid="123456789")
        self.token = {
            "access_token": "test-access",
            "expires_in": 300,
            "scope": "prober.records.read",
        }
        self.metadata = {
            "issuer": self.oauth.origin,
            "token_endpoint": self.oauth.origin + "/oauth/token",
            "device_authorization_endpoint": self.oauth.origin
            + "/oauth/device_authorization",
        }
        self.api = MaimaiAPI()
        self.api.configure(
            MaiConfig(
                divingfish_client_id="operator-app",
                divingfish_client_secret="test-secret",
                maimaidxproberproxy=True,
            )
        )
        self.user = {
            "nickname": "test",
            "username": "test-user",
            "rating": 0,
            "additional_rating": 0,
            "plate": "",
            "records": [],
        }

    def test_migrated_identity_uses_exact_digest(self):
        expected = hashlib.sha256(b"operator-app:123456789").hexdigest()
        self.assertEqual(self.subject, "ref:" + expected)
        self.assertEqual(
            self.oauth.subject(username="another-user"), "username:another-user"
        )
        self.assertEqual(
            self.oauth.subject(qqid="123456789", username="another-user"),
            "username:another-user",
        )

    async def test_existing_consent_queries_without_device_authorization(self):
        self.oauth._request = AsyncMock(return_value=self.token)
        self.oauth.authorize = AsyncMock()
        self.assertEqual(await self.oauth.access_token(self.subject), "test-access")
        self.oauth.authorize.assert_not_awaited()
        self.assertEqual(
            self.oauth._request.await_args.args[1],
            {
                "grant_type": "urn:diving-fish:params:oauth:grant-type:on-behalf-of",
                "subject": self.subject,
                "scope": "prober.records.read",
            },
        )

    async def test_concurrent_queries_reuse_one_exchange(self):
        self.oauth._request = AsyncMock(return_value=self.token)
        tokens = await asyncio.gather(
            *(self.oauth.access_token(self.subject) for _ in range(10))
        )
        self.assertEqual(tokens, ["test-access"] * 10)
        self.oauth._request.assert_awaited_once()

    async def test_expired_and_forced_tokens_are_exchanged_again(self):
        self.oauth._request = AsyncMock(return_value=self.token)
        await self.oauth.access_token(self.subject)
        self.oauth._tokens[self.subject] = ("expired", 0)
        await self.oauth.access_token(self.subject)
        await self.oauth.access_token(self.subject, force=True)
        self.assertEqual(self.oauth._request.await_count, 3)

    async def test_users_do_not_share_access_tokens(self):
        self.oauth._request = AsyncMock(
            side_effect=[self.token, dict(self.token, access_token="other-access")]
        )
        first = await self.oauth.access_token(self.subject)
        second = await self.oauth.access_token(self.oauth.subject(qqid="987654321"))
        self.assertNotEqual(first, second)

    async def test_missing_credentials_fail_before_network(self):
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_oauth.ClientSession"
        ) as session:
            with self.assertRaisesRegex(OAuthError, "尚未配置"):
                await DivingFishOAuth().access_token(self.subject)
            session.assert_not_called()

    async def test_incomplete_or_unscoped_token_is_not_cached(self):
        for data in (
            {},
            dict(self.token, scope="openid"),
            dict(self.token, expires_in=0),
            dict(self.token, expires_in="bad"),
        ):
            with self.subTest(data=data):
                self.oauth._request = AsyncMock(return_value=data)
                with self.assertRaises(OAuthError):
                    await self.oauth.access_token(self.subject)
                self.assertNotIn(self.subject, self.oauth._tokens)

    async def test_device_authorization_binds_and_displays_masked_qq(self):
        self.oauth._request = AsyncMock(
            return_value={
                "verification_uri": self.oauth.origin + "/device",
                "verification_uri_complete": self.oauth.origin
                + "/device?user_code=TEST-CODE",
                "user_code": "TEST-CODE",
                "device_code": "never-display",
                "expires_in": 600,
            }
        )
        result = await self.oauth.authorize("123456789")
        form = self.oauth._request.await_args.args[1]
        self.assertEqual(form["subject_ref"], self.subject[4:])
        self.assertEqual(form["binding_label"], "QQ 12*****89")
        self.assertEqual(result["binding_label"], form["binding_label"])
        self.assertNotIn("device_code", result)
        self.assertNotIn("client_secret", result)

    async def test_untrusted_authorization_link_is_rejected(self):
        self.oauth._request = AsyncMock(
            return_value={
                "verification_uri": "https://example.org/device",
                "verification_uri_complete": "https://example.org/device",
            }
        )
        with self.assertRaisesRegex(OAuthError, "链接无效"):
            await self.oauth.authorize("123456789")

    async def test_discovery_and_form_credentials_use_official_origin(self):
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        discovery = MagicMock(status=200)
        discovery.__aenter__ = AsyncMock(return_value=discovery)
        discovery.__aexit__ = AsyncMock(return_value=False)
        discovery.json = AsyncMock(return_value=self.metadata)
        response = MagicMock(status=200)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        response.json = AsyncMock(return_value=self.token)
        session.get.return_value = discovery
        session.post.return_value = response
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_oauth.ClientSession",
            return_value=session,
        ):
            await self.oauth.access_token(self.subject)
        session.get.assert_called_once_with(
            self.oauth.origin + "/.well-known/openid-configuration",
            allow_redirects=False,
        )
        self.assertEqual(
            session.post.call_args.args[0], self.metadata["token_endpoint"]
        )
        form = session.post.call_args.kwargs["data"]
        self.assertEqual(form["client_id"], "operator-app")
        self.assertEqual(form["client_secret"], "test-secret")
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])

    async def test_discovery_cannot_send_credentials_to_another_origin(self):
        self.oauth._metadata = dict(
            self.metadata, token_endpoint="https://example.org/token"
        )
        self.oauth._metadata_expires = float("inf")
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_oauth.ClientSession"
        ) as constructor:
            session = constructor.return_value.__aenter__.return_value
            with self.assertRaisesRegex(OAuthError, "端点无效"):
                await self.oauth.access_token(self.subject)
            session.post.assert_not_called()

    async def test_oauth_errors_are_actionable_and_do_not_echo_credentials(self):
        self.oauth._metadata = self.metadata
        self.oauth._metadata_expires = float("inf")
        for status, error, expected in (
            (400, "consent_required", "mai授权"),
            (401, "invalid_client", "凭据无效"),
            (400, "invalid_scope", "prober.records.read"),
            (400, "unauthorized_client", "补全应用资料"),
            (429, "slow_down", "配额"),
        ):
            with self.subTest(error=error):
                session = MagicMock()
                session.__aenter__ = AsyncMock(return_value=session)
                session.__aexit__ = AsyncMock(return_value=False)
                response = MagicMock(status=status)
                response.__aenter__ = AsyncMock(return_value=response)
                response.__aexit__ = AsyncMock(return_value=False)
                response.json = AsyncMock(
                    return_value={"error": error, "error_description": "test-secret"}
                )
                session.post.return_value = response
                with patch(
                    "astrbot_plugin_maibot.libraries.maimaidx_oauth.ClientSession",
                    return_value=session,
                ):
                    with self.assertRaises(OAuthError) as failure:
                        await self.oauth.access_token(self.subject)
                self.assertIn(expected, str(failure.exception))
                self.assertNotIn("test-secret", str(failure.exception))
                self.assertNotIn("不存在", str(failure.exception))

    async def test_records_api_removes_identity_from_resource_parameters(self):
        self.api._requestmai = AsyncMock(return_value=self.user)
        await self.api.query_user_records(qqid="123456789")
        self.api._requestmai.assert_awaited_once_with(
            "GET", "/player/records", subject=self.subject
        )

    async def test_plate_api_keeps_only_version_in_body(self):
        self.api._requestmai = AsyncMock(return_value={"verlist": []})
        await self.api.query_user_plate(username="test-user", version=["maimai"])
        self.api._requestmai.assert_awaited_once_with(
            "POST",
            "/player/plate",
            subject="username:test-user",
            json={"version": ["maimai"]},
        )

    async def test_song_api_keeps_only_music_ids_in_body(self):
        self.api._requestmai = AsyncMock(return_value={"11167": [], "11661": []})
        await self.api.query_user_record(qqid="123456789", music_id=[11167, 11661])
        self.api._requestmai.assert_awaited_once_with(
            "POST",
            "/player/record",
            subject=self.subject,
            json={"music_id": [11167, 11661]},
        )

    async def test_public_b50_still_works_without_oauth(self):
        self.api._requestmai = AsyncMock(
            return_value=dict(self.user, charts={"sd": [], "dx": []})
        )
        self.api.oauth.access_token = AsyncMock()
        await self.api.query_user_b50(qqid="123456789")
        self.api._requestmai.assert_awaited_once_with(
            "POST", "/query/player", json={"qq": "123456789", "b50": True}
        )
        self.api.oauth.access_token.assert_not_awaited()

    async def test_rejected_access_token_is_refreshed_once_without_proxy_leak(self):
        self.api.oauth.access_token = AsyncMock(
            side_effect=["old-access", "fresh-access"]
        )
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        responses = []
        for status in (401, 200):
            response = MagicMock(status=status)
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            response.json = AsyncMock(return_value=self.user)
            responses.append(response)
        session.request.side_effect = responses
        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_api_data.ClientSession",
            return_value=session,
        ):
            await self.api.query_user_records(qqid="123456789")
        self.assertEqual(
            self.api.oauth.access_token.await_args_list[-1].kwargs, {"force": True}
        )
        for call in session.request.call_args_list:
            self.assertEqual(call.args[1], self.api.MaiProberAPI + "/player/records")
            self.assertNotIn("params", call.kwargs)
        self.assertEqual(
            session.request.call_args.kwargs["headers"],
            {"Authorization": "Bearer fresh-access"},
        )

    async def test_resource_permission_and_quota_errors_do_not_retry(self):
        for status in (403, 429, 503):
            with self.subTest(status=status):
                self.api.oauth.access_token = AsyncMock(return_value="test-access")
                session = MagicMock()
                session.__aenter__ = AsyncMock(return_value=session)
                session.__aexit__ = AsyncMock(return_value=False)
                response = MagicMock(status=status)
                response.__aenter__ = AsyncMock(return_value=response)
                response.__aexit__ = AsyncMock(return_value=False)
                session.request.return_value = response
                with patch(
                    "astrbot_plugin_maibot.libraries.maimaidx_api_data.ClientSession",
                    return_value=session,
                ):
                    with self.assertRaises(OAuthError):
                        await self.api.query_user_records(qqid="123456789")
                session.request.assert_called_once()

    async def test_authorization_errors_reach_existing_query_handlers(self):
        from astrbot_plugin_maibot.libraries.maimaidx_music_info import (
            draw_music_play_data,
        )

        with patch(
            "astrbot_plugin_maibot.libraries.maimaidx_music_info.maiApi.query_user_record",
            new=AsyncMock(side_effect=OAuthError("请发送 mai授权")),
        ):
            self.assertEqual(
                await draw_music_play_data(qqid="123456789", music_id="11167"),
                "请发送 mai授权",
            )

    async def test_group_queries_do_not_cache_failed_authorization(self):
        from astrbot_plugin_maibot.libraries import maimaidx_group_ranking as ranking

        ranking._SONG_RECORD_CACHE.clear()
        music = SimpleNamespace(id="11167")
        with patch.object(
            ranking.maiApi,
            "query_user_record",
            new=AsyncMock(side_effect=OAuthError("请发送 mai授权")),
        ):
            result = await ranking._query_song_record(
                "123456789", music, 3, asyncio.Semaphore(1)
            )
        self.assertIsNone(result)
        self.assertNotIn(("123456789", "11167", 3), ranking._SONG_RECORD_CACHE)


if __name__ == "__main__":
    unittest.main()
