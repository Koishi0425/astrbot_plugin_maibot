import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

import aiofiles
from astrbot.api.event import AstrMessageEvent

from .. import log, user_qq_bindings_file

_QQ_PATTERN = re.compile(r"^[1-9][0-9]{4,11}$")
_BINDINGS_LOCK = asyncio.Lock()


@dataclass
class MaimaiIdentity:
    qqid: Optional[str] = None
    username: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.qqid or self.username) and not self.error


def is_numeric_qq(value) -> bool:
    return bool(_QQ_PATTERN.fullmatch(str(value or "").strip()))


def binding_required_message(target: str = "你") -> str:
    return f"当前平台无法直接获取{target}的 QQ 号，请先发送：\n绑定QQ 你的QQ号"


def _get_platform_name(event: AstrMessageEvent) -> str:
    for attr in ("get_platform_name", "get_platform_id"):
        func = getattr(event, attr, None)
        if callable(func):
            try:
                value = func()
                if value:
                    return str(value)
            except Exception:
                pass
    message_obj = getattr(event, "message_obj", None)
    for attr in ("platform_name", "platform_id", "adapter", "adapter_name"):
        value = getattr(message_obj, attr, None)
        if value:
            return str(value)
    return "unknown"


def get_identity_key(event: AstrMessageEvent, user_id=None) -> str:
    platform = _get_platform_name(event).replace(":", "_")
    uid = str(user_id if user_id is not None else event.get_sender_id())
    return f"{platform}:{uid}"


async def _load_bindings() -> Dict[str, dict]:
    if not user_qq_bindings_file.exists():
        return {}
    try:
        async with aiofiles.open(user_qq_bindings_file, "r", encoding="utf-8") as f:
            content = await f.read()
        if not content.strip():
            return {}
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning(f"读取QQ绑定文件失败: {e}")
        return {}


async def _save_bindings(bindings: Dict[str, dict]) -> None:
    user_qq_bindings_file.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(user_qq_bindings_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(bindings, ensure_ascii=False, indent=4))


async def bind_event_qq(event: AstrMessageEvent, qq: str) -> None:
    qq = str(qq).strip()
    if not is_numeric_qq(qq):
        raise ValueError("QQ号格式不正确，请输入5-12位纯数字QQ号")
    async with _BINDINGS_LOCK:
        bindings = await _load_bindings()
        bindings[get_identity_key(event)] = {
            "qq": qq,
            "platform": _get_platform_name(event),
            "user_id": str(event.get_sender_id()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await _save_bindings(bindings)


async def get_bound_qq(event: AstrMessageEvent, user_id=None) -> Optional[str]:
    bindings = await _load_bindings()
    item = bindings.get(get_identity_key(event, user_id))
    if isinstance(item, dict):
        qq = item.get("qq")
    else:
        qq = item
    return str(qq) if is_numeric_qq(qq) else None


async def unbind_event_qq(event: AstrMessageEvent) -> bool:
    async with _BINDINGS_LOCK:
        bindings = await _load_bindings()
        key = get_identity_key(event)
        existed = key in bindings
        if existed:
            del bindings[key]
            await _save_bindings(bindings)
        return existed


def extract_at_user_id(event: AstrMessageEvent) -> Optional[str]:
    if not event.message_obj or not event.message_obj.message:
        return None
    for component in event.message_obj.message:
        if hasattr(component, "qq") and component.qq:
            return str(component.qq)
        if hasattr(component, "user_id") and component.user_id:
            return str(component.user_id)
        if hasattr(component, "type") and component.type == "at" and hasattr(component, "data"):
            data = component.data or {}
            for key in ("qq", "user_id", "id", "openid"):
                if key in data and data[key]:
                    return str(data[key])
    return None


async def resolve_user_qq(event: AstrMessageEvent, user_id=None, target: str = "你") -> MaimaiIdentity:
    raw_id = str(user_id if user_id is not None else event.get_sender_id())
    if is_numeric_qq(raw_id):
        return MaimaiIdentity(qqid=raw_id)
    bound = await get_bound_qq(event, raw_id)
    if bound:
        return MaimaiIdentity(qqid=bound)
    return MaimaiIdentity(error=binding_required_message(target))


async def resolve_sender_qq(event: AstrMessageEvent) -> MaimaiIdentity:
    return await resolve_user_qq(event)


async def resolve_at_qq(event: AstrMessageEvent) -> MaimaiIdentity:
    at_user_id = extract_at_user_id(event)
    if not at_user_id:
        return MaimaiIdentity()
    return await resolve_user_qq(event, at_user_id, "被@用户")


async def resolve_sender_qq_optional(event: AstrMessageEvent) -> Optional[str]:
    identity = await resolve_sender_qq(event)
    return identity.qqid if identity.ok else None
