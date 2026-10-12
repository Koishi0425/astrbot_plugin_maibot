import asyncio
import hashlib
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout


class OAuthError(Exception):
    """Represent an OAuth failure with a message safe to show to users."""


class DivingFishOAuth:
    """Access an operator-owned confidential Diving-Fish application."""

    origin = "https://auth.diving-fish.com"
    scope = "prober.records.read"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self._metadata = {}
        self._metadata_expires = 0.0
        self._tokens = {}
        self._lock = asyncio.Lock()

    def subject(self, *, qqid=None, username=None) -> str:
        """Resolve a lookup key without the retiring qq: subject format.

        Args:
            qqid: Existing QQ binding, preserving the migration digest input.
            username: Prober username supplied for a query of another player.

        Returns:
            A ref: digest or username: lookup key.

        Raises:
            ValueError: Neither identifier was supplied.
        """
        if username:
            return "username:" + str(username).strip()
        if qqid:
            digest = hashlib.sha256(f"{self.client_id}:{qqid}".encode()).hexdigest()
            return "ref:" + digest
        raise ValueError("请提供 QQ 号或查分器用户名")

    async def _request(self, endpoint: str, data: dict) -> dict:
        """Discover official endpoints and submit a confidential-client request.

        Args:
            endpoint: Metadata key identifying the requested OAuth endpoint.
            data: Form parameters excluding application credentials.

        Returns:
            The successful JSON response.

        Raises:
            OAuthError: Configuration, discovery, transport or OAuth failed.
        """
        if not self.client_id or not self.client_secret:
            raise OAuthError(
                "水鱼 OAuth 尚未配置，请管理员填写 client_id 和 client_secret。"
            )
        try:
            async with ClientSession(timeout=ClientTimeout(total=30)) as session:
                if time.monotonic() >= self._metadata_expires:
                    async with session.get(
                        self.origin + "/.well-known/openid-configuration",
                        allow_redirects=False,
                    ) as response:
                        if response.status != 200:
                            raise OAuthError("水鱼 OAuth 服务发现失败，请稍后重试。")
                        metadata = await response.json()
                    if metadata.get("issuer", "").rstrip("/") != self.origin:
                        raise OAuthError("水鱼 OAuth 服务信息不匹配，请联系管理员。")
                    self._metadata = metadata
                    self._metadata_expires = time.monotonic() + 3600
                target = self._metadata.get(endpoint, "")
                address = urlsplit(target)
                if (
                    address.scheme != "https"
                    or address.netloc != urlsplit(self.origin).netloc
                    or address.username
                    or address.password
                ):
                    raise OAuthError("水鱼 OAuth 端点无效，请联系管理员。")
                form = dict(
                    data, client_id=self.client_id, client_secret=self.client_secret
                )
                async with session.post(
                    target, data=form, allow_redirects=False
                ) as response:
                    result = await response.json()
                    if response.status == 200 and "error" not in result:
                        return result
                    error = result.get("error", "")
                    if error == "consent_required":
                        raise OAuthError(
                            "该玩家尚未授权当前水鱼应用，请本人发送 mai授权 并完成确认。"
                        )
                    if error == "invalid_client":
                        raise OAuthError(
                            "水鱼 OAuth 应用凭据无效，请管理员检查机密客户端配置。"
                        )
                    if error == "invalid_scope":
                        raise OAuthError(
                            "水鱼应用未获成绩读取权限，请管理员申请 prober.records.read。"
                        )
                    if error == "unauthorized_client":
                        raise OAuthError(
                            "水鱼应用不支持此授权方式，请管理员补全应用资料并启用设备码和换票。"
                        )
                    if response.status == 429 or error == "slow_down":
                        raise OAuthError("水鱼请求过于频繁或已达配额，请稍后重试。")
                    raise OAuthError("水鱼 OAuth 请求失败，请稍后重试或联系管理员。")
        except (ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            raise OAuthError("水鱼 OAuth 服务暂时不可用，请稍后重试。") from exc

    async def access_token(self, subject: str, *, force: bool = False) -> str:
        """Reuse short-lived OBO tokens and serialize concurrent exchanges.

        Args:
            subject: User lookup key returned by subject().
            force: Discard a token rejected by the resource server.

        Returns:
            A Bearer access token held only in memory.

        Raises:
            OAuthError: The application or user's authorization is invalid.
        """
        async with self._lock:
            now = time.monotonic()
            self._tokens = {
                key: item for key, item in self._tokens.items() if item[1] > now
            }
            if force:
                self._tokens.pop(subject, None)
            if subject not in self._tokens:
                result = await self._request(
                    "token_endpoint",
                    {
                        "grant_type": "urn:diving-fish:params:oauth:grant-type:on-behalf-of",
                        "subject": subject,
                        "scope": self.scope,
                    },
                )
                token = result.get("access_token")
                if not isinstance(token, str) or not token:
                    raise OAuthError("水鱼未返回有效的访问令牌，请稍后重试。")
                try:
                    lifetime = float(result["expires_in"])
                except (KeyError, ValueError, TypeError) as exc:
                    raise OAuthError("水鱼返回的令牌有效期无效，请稍后重试。") from exc
                if lifetime <= 0 or self.scope not in result.get("scope", "").split():
                    raise OAuthError("水鱼令牌未获成绩读取权限，请重新发送 mai授权。")
                self._tokens[subject] = (
                    token,
                    time.monotonic() + lifetime - min(30, lifetime / 10),
                )
            return self._tokens[subject][0]

    async def authorize(self, qqid: str) -> dict:
        """Create a device consent link bound to a masked existing QQ identity.

        Args:
            qqid: QQ identifier of the requesting user.

        Returns:
            Verification URLs, user code and expiry for display to that user.

        Raises:
            OAuthError: The application cannot start device authorization.
        """
        label = f"QQ {qqid[:2]}{'*' * (len(qqid) - 4)}{qqid[-2:]}"
        result = await self._request(
            "device_authorization_endpoint",
            {
                "scope": self.scope,
                "subject_ref": self.subject(qqid=qqid)[4:],
                "binding_label": label,
            },
        )
        for field in ("verification_uri", "verification_uri_complete"):
            address = urlsplit(result.get(field, ""))
            if (
                address.scheme != "https"
                or address.netloc != urlsplit(self.origin).netloc
            ):
                raise OAuthError("水鱼返回的授权链接无效，请稍后重试。")
        if not result.get("user_code") or not result.get("expires_in"):
            raise OAuthError("水鱼返回的授权信息不完整，请稍后重试。")
        # Consent and ref mapping are established immediately on approval.
        # Confidential clients can use OBO on the next query without polling.
        return {
            key: result[key]
            for key in (
                "verification_uri",
                "verification_uri_complete",
                "user_code",
                "expires_in",
            )
        } | {"binding_label": label}
