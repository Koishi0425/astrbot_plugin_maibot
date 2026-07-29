"""Process-local bridge to an optional private SDGB Tech AstrBot plugin.

The public repository deliberately contains only this versioned interface.  It
does not import SDGB code or know private capability names.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from typing import Any, Protocol


PROTOCOL_VERSION = 1
ALLOWED_METHODS = {
    "status",
    "set_enabled",
    "capabilities",
    "submit_qr",
    "credential_status",
    "revoke_credential",
    "preview",
    "confirm",
    "job_status",
    "job_result_page",
    "cancel",
}
SENSITIVE_KEYS = {
    "token", "qrcode", "qr_code", "keychip", "keychip_id", "userid",
    "user_id", "password", "secret", "payload", "request", "response",
    "aes_key", "aes_iv", "stdout", "stderr", "command", "path",
}
_backend: "SDGBTechBackend | None" = None
QR_PATTERN = re.compile(r"SGWCMAID[^\s]{8,504}")
IMPORT_TOKEN_COMMAND_PATTERN = re.compile(r"(?i)(上传水鱼\s+)(\S+)")


class BridgeError(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class SDGBTechBackend(Protocol):
    protocol_version: int


def redact_qr_text(value: str) -> str:
    value = QR_PATTERN.sub("SGWCMAID[REDACTED]", str(value))
    return IMPORT_TOKEN_COMMAND_PATTERN.sub(r"\1[REDACTED]", value)


class _QRLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_qr_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_qr_text(item) if isinstance(item, str) else item
                for item in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_qr_text(item) if isinstance(item, str) else item
                for key, item in record.args.items()
            }
        return True


_qr_log_filter: _QRLogFilter | None = None


def install_qr_log_redaction() -> None:
    global _qr_log_filter
    if _qr_log_filter is not None:
        return
    _qr_log_filter = _QRLogFilter()
    root = logging.getLogger()
    root.addFilter(_qr_log_filter)
    for handler in root.handlers:
        handler.addFilter(_qr_log_filter)


def register_backend(backend: SDGBTechBackend) -> None:
    global _backend
    if getattr(backend, "protocol_version", None) != PROTOCOL_VERSION:
        raise BridgeError("PROTOCOL_MISMATCH", "SDGB Tech 后端协议不兼容")
    _backend = backend


def unregister_backend(backend: SDGBTechBackend) -> None:
    global _backend
    if _backend is backend:
        _backend = None


def backend_available() -> bool:
    return _backend is not None


async def call_backend(method: str, **kwargs: Any) -> Any:
    if method not in ALLOWED_METHODS:
        raise BridgeError("UNKNOWN_METHOD", "不支持的 SDGB Tech 后端调用")
    backend = _backend
    if backend is None:
        raise BridgeError(
            "BACKEND_UNAVAILABLE",
            "私有 SDGB Tech 插件未安装、未加载或版本不兼容",
        )
    handler = getattr(backend, method, None)
    if not callable(handler):
        raise BridgeError("PROTOCOL_MISMATCH", "SDGB Tech 后端接口不完整")
    try:
        result = handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
    except BridgeError:
        raise
    except Exception as exc:
        safe_message = getattr(exc, "safe_message", "私有 SDGB Tech 后端拒绝请求")
        code = getattr(exc, "code", "BACKEND_ERROR")
        raise BridgeError(str(code), str(safe_message)) from exc
    _validate_safe_result(result)
    return result


def _validate_safe_result(value: Any) -> None:
    if _contains_sensitive_key(value):
        raise BridgeError("UNSAFE_RESPONSE", "SDGB Tech 后端返回了禁止字段")
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise BridgeError("INVALID_RESPONSE", "SDGB Tech 后端响应无法序列化") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise BridgeError("INVALID_RESPONSE", "SDGB Tech 后端响应过大")


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(
                char for char in str(key).lower()
                if char.isalnum() or char == "_"
            )
            if normalized in SENSITIVE_KEYS or _contains_sensitive_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False
