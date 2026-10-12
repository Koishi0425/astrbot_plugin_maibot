from typing import Any, Dict, List, Optional, Union

from aiohttp import ClientSession, ClientTimeout
from pydantic import BaseModel

from .maimaidx_error import (
    MusicNotPlayError,
    ServerError,
    UnknownError,
    UserDisabledQueryError,
    UserNotExistsError,
    UserNotFoundError,
)
from .maimaidx_identity import is_numeric_qq
from .maimaidx_model import (
    Alias,
    AliasStatus,
    PlayInfoDefault,
    PlayInfoDev,
    UserInfo,
    UserInfoDev,
    UserRanking,
)
from .maimaidx_oauth import DivingFishOAuth, OAuthError


class MaiConfig(BaseModel):
    divingfish_client_id: str = ""
    divingfish_client_secret: str = ""
    maimaidxproberproxy: bool = False
    maimaidxaliasproxy: bool = False
    maimaidxaliaspush: bool = False
    maimaidxaliasvoting: bool = False
    # Broadcast only to enabled groups, or to all groups except disabled ones.
    maimaidxaliaswhitelist: bool = False
    saveinmem: Optional[bool] = True
    resource_local_path: str = ""
    resource_source_url: str = ""
    resource_check_on_startup: bool = True
    arcade_enabled: bool = False


class MaimaiAPI:
    MaiProxyAPI = "https://proxy.yuzuchan.site"

    MaiProberAPI = "https://www.diving-fish.com/api/maimaidxprober"
    MaiCover = "https://www.diving-fish.com/covers"
    MaiAliasAPI = "https://www.yuzuchan.moe/api/v2"
    QQAPI = "http://q1.qlogo.cn/g"

    def __init__(self) -> None:
        """Initialize public clients and an unconfigured OAuth client."""
        self.config: MaiConfig = MaiConfig()
        self.oauth = DivingFishOAuth()
        self.MaiProberProxyAPI = None
        self.MaiAliasProxyAPI = None
        self.load_proxy()

    def load_config(self) -> MaiConfig:
        """Return defaults for old callers.

        Runtime configuration is injected from AstrBot WebUI by the plugin
        entrypoint. static/config.json is intentionally no longer read.
        """
        return MaiConfig()

    def configure(self, config: Union[MaiConfig, Dict[str, Any], None] = None) -> None:
        """Apply WebUI settings and discard tokens issued for previous credentials.

        Args:
            config: Validated settings, raw settings or defaults.
        """
        if isinstance(config, MaiConfig):
            self.config = config
        else:
            self.config = MaiConfig.model_validate(config or {})
        self.oauth = DivingFishOAuth(
            self.config.divingfish_client_id, self.config.divingfish_client_secret
        )
        self.load_proxy()

    def load_proxy(self) -> None:
        """Select origins for public prober requests and v2 alias requests."""
        self.MaiProberProxyAPI = (
            self.MaiProberAPI
            if not self.config.maimaidxproberproxy
            else self.MaiProxyAPI + "/maimaidxprober"
        )
        self.MaiAliasProxyAPI = (
            self.MaiAliasAPI
            if not self.config.maimaidxaliasproxy
            else "https://www.yuzuchan.cn/api/v2"
        )

    async def _requestalias(self, method: str, endpoint: str, **kwargs):
        """Request v2 alias data, retaining user-facing validation messages.

        Args:
            method: HTTP method.
            endpoint: Path relative to the v2 service.
            **kwargs: Request parameters or JSON body.

        Returns:
            Decoded JSON data or a validation message object.

        Raises:
            ServerError: The alias service failed.
            UnknownError: The response was not supported.
        """
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            async with session.request(
                method, self.MaiAliasProxyAPI + endpoint, **kwargs
            ) as res:
                data = await res.json()
                if res.status == 200 or (
                    400 <= res.status < 500
                    and isinstance(data, dict)
                    and "message" in data
                ):
                    return data
                if res.status >= 500:
                    raise ServerError
                raise UnknownError

    async def _requestmai(
        self, method: str, endpoint: str, *, subject: Optional[str] = None, **kwargs
    ):
        """Request public data or OAuth-protected records on the official origin.

        Args:
            method: HTTP method.
            endpoint: Prober resource path.
            subject: Authorized user lookup key; absent for public endpoints.
            **kwargs: Resource-specific request parameters.

        Returns:
            Decoded prober JSON data.

        Raises:
            OAuthError: Consent, credentials, permissions or quotas prevent access.
            UserNotFoundError: A public query found no matching player.
            UserDisabledQueryError: The player disabled public queries.
            UnknownError: The resource server failed.
        """
        base = self.MaiProberAPI if subject else self.MaiProberProxyAPI
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            for attempt in range(2):
                headers = None
                if subject:
                    token = await self.oauth.access_token(subject, force=attempt > 0)
                    headers = {"Authorization": f"Bearer {token}"}
                async with session.request(
                    method,
                    base + endpoint,
                    headers=headers,
                    allow_redirects=False,
                    **kwargs,
                ) as res:
                    if res.status == 200:
                        return await res.json()
                    if subject:
                        if res.status == 401 and attempt == 0:
                            continue
                        if res.status == 401:
                            raise OAuthError("水鱼授权已失效，请重新发送 mai授权。")
                        if res.status == 403:
                            raise OAuthError(
                                "水鱼拒绝访问，请确认已同意查分器协议并授权成绩读取权限。"
                            )
                        if res.status == 429:
                            raise OAuthError("水鱼查询已达配额，请稍后重试。")
                        if res.status == 400:
                            raise OAuthError(
                                "水鱼成绩请求被拒绝，请检查账号和查询参数。"
                            )
                        raise OAuthError("水鱼成绩服务暂时不可用，请稍后重试。")
                    if res.status == 400:
                        error = await res.json()
                        if error.get("message") == "user not exists":
                            raise UserNotExistsError
                        raise UserNotFoundError
                    if res.status == 403:
                        raise UserDisabledQueryError
                    raise UnknownError

    async def music_data(self):
        """获取曲目数据"""
        return await self._requestmai("GET", "/music_data")

    async def chart_stats(self):
        """获取单曲数据"""
        return await self._requestmai("GET", "/chart_stats")

    async def query_user_b50(
        self, *, qqid: Optional[int] = None, username: Optional[str] = None
    ) -> UserInfo:
        """
        获取玩家B50

        Params:
            `qqid`: QQ号
            `username`: 用户名
        Returns:
            `UserInfo` b50数据模型
        """
        json = {}
        if qqid:
            if not is_numeric_qq(qqid):
                raise ValueError("qqid must be numeric")
            json["qq"] = qqid
        if username:
            json["username"] = username
        json["b50"] = True

        return UserInfo.model_validate(
            await self._requestmai("POST", "/query/player", json=json)
        )

    async def query_user_plate(
        self, *, qqid=None, username=None, version: Optional[List[str]] = None
    ) -> List[PlayInfoDefault]:
        """Read version-filtered records using the player's OAuth identity.

        Args:
            qqid: QQ identifier of an authorized player.
            username: Prober username of an authorized player.
            version: Version names to include.

        Returns:
            Records suitable for plate and completion-table queries.
        """
        subject = self.oauth.subject(qqid=qqid, username=username)
        result = await self._requestmai(
            "POST", "/player/plate", subject=subject, json={"version": version or []}
        )
        return [PlayInfoDefault.model_validate(d) for d in result["verlist"]]

    async def query_user_records(self, *, qqid=None, username=None) -> UserInfoDev:
        """Read the authorized player's complete record collection.

        Args:
            qqid: QQ identifier of an authorized player.
            username: Prober username of an authorized player.

        Returns:
            Player information and all chart records.
        """
        subject = self.oauth.subject(qqid=qqid, username=username)
        result = await self._requestmai("GET", "/player/records", subject=subject)
        return UserInfoDev.model_validate(result)

    async def query_user_record(
        self,
        *,
        qqid=None,
        username=None,
        music_id: Union[str, int, List[Union[str, int]]],
    ) -> List[PlayInfoDev]:
        """Read an authorized player's records for selected songs.

        Args:
            qqid: QQ identifier of an authorized player.
            username: Prober username of an authorized player.
            music_id: One song ID or a list of IDs.

        Returns:
            Available records for the requested songs.

        Raises:
            MusicNotPlayError: No requested song was played.
        """
        subject = self.oauth.subject(qqid=qqid, username=username)
        result = await self._requestmai(
            "POST", "/player/record", subject=subject, json={"music_id": music_id}
        )
        if not result:
            raise MusicNotPlayError
        return [
            PlayInfoDev.model_validate(d)
            for records in result.values()
            for d in records
        ]

    async def rating_ranking(self) -> List[UserRanking]:
        """
        获取查分器排行榜

        Returns:
            `List[UserRanking]` 按`ra`从高到低排序后的查分器排行模型列表
        """
        result = await self._requestmai("GET", "/rating_ranking")
        return sorted(
            [UserRanking.model_validate(u) for u in result],
            key=lambda x: x.ra,
            reverse=True,
        )

    async def get_plate_json(self) -> Dict[str, List[int]]:
        """Return the public version-to-plate catalog."""
        return await self._requestalias("GET", "/maimaidx/music/get_plate")

    async def get_alias(self) -> list:
        """Return aliases normalized to the existing local cache schema."""
        result = await self._requestalias("GET", "/aliases/maimaidx/aliases")
        return [Alias.model_validate(item).model_dump() for item in result]

    async def get_songs(self, name: str) -> Union[List[AliasStatus], List[Alias]]:
        """Search aliases and ongoing votes by name.

        Args:
            name: Alias to search for.

        Returns:
            Matching songs, votes, or an empty list.
        """
        result = await self._requestalias(
            "GET", "/aliases/maimaidx/songs", params={"name": name}
        )
        if "message" in result:
            return []
        model = AliasStatus if result["type"] == "ongoing" else Alias
        return [model.model_validate(item) for item in result["data"]]

    async def get_songs_alias(self, song_id: int) -> Union[Alias, str]:
        """Look up the aliases of one song.

        Args:
            song_id: Song ID.

        Returns:
            Alias information or the service's validation message.
        """
        result = await self._requestalias(
            "GET", "/aliases/maimaidx/aliases", params={"song_id": song_id}
        )
        return (
            result["message"] if "message" in result else Alias.model_validate(result)
        )

    async def get_alias_status(self) -> List[AliasStatus]:
        """Return all ongoing alias votes."""
        result = await self._requestalias(
            "GET", "/aliases/maimaidx/votes", params={"status": "ongoing"}
        )
        return [AliasStatus.model_validate(item) for item in result]

    async def post_alias(
        self, song_id: int, aliasname: str, user_id: int, group_id: int
    ) -> str:
        """Submit a song alias through the v2 service.

        Args:
            song_id: Song ID.
            aliasname: Proposed alias.
            user_id: Applicant's QQ identifier.
            group_id: Originating group identifier.

        Returns:
            The service's user-facing result message.
        """
        result = await self._requestalias(
            "POST",
            "/aliases/maimaidx/apply",
            json={
                "song_id": song_id,
                "apply_alias": aliasname,
                "apply_uid": user_id,
                "group_id": group_id,
            },
        )
        return result["message"]

    async def post_agree_user(self, tag: str, user_id: int) -> str:
        """Submit a vote for a pending alias.

        Args:
            tag: Vote identifier.
            user_id: Voter's QQ identifier.

        Returns:
            The service's user-facing result message.
        """
        result = await self._requestalias(
            "POST", "/aliases/maimaidx/votes", json={"tag": tag, "agree_user": user_id}
        )
        return result["message"]

    async def qqlogo(self, qqid: int = None, icon: str = None) -> Optional[bytes]:
        """获取QQ头像"""
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            if qqid:
                if not is_numeric_qq(qqid):
                    raise ValueError("qqid must be numeric")
                params = {"b": "qq", "nk": qqid, "s": 100}
                res = await session.request("GET", self.QQAPI, params=params)
            elif icon:
                res = await session.request("GET", icon)
            else:
                return None
            return await res.read()


maiApi = MaimaiAPI()
