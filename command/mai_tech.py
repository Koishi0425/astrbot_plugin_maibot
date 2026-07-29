"""Formatting and strict argument helpers for the generic private backend."""

from __future__ import annotations

import re
from typing import Any


CAPABILITY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
VALUE_RE = re.compile(r"[A-Za-z0-9_.:+-]{1,64}")


def parse_parameters(text: str) -> dict[str, str]:
    normalized = str(text or "").strip()
    if not normalized or normalized == "-":
        return {}
    if len(normalized) > 256:
        raise ValueError("参数过长")
    result: dict[str, str] = {}
    for token in normalized.split(","):
        if "=" not in token:
            raise ValueError("参数必须使用 key=value，并以逗号分隔")
        key, value = (part.strip() for part in token.split("=", 1))
        if not KEY_RE.fullmatch(key) or not VALUE_RE.fullmatch(value):
            raise ValueError("参数包含不允许的字符")
        if key in result:
            raise ValueError(f"参数 {key} 不能重复")
        result[key] = value
    return result


def validate_capability(value: str) -> str:
    normalized = str(value).strip()
    if not CAPABILITY_RE.fullmatch(normalized):
        raise ValueError("能力名称格式无效")
    return normalized


def validate_identifier(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{label}格式无效")
    return normalized


def format_capabilities(items: list[dict[str, Any]]) -> str:
    rows = ["私有 SDGB Tech 能力："]
    for item in items:
        mode = "写入" if item.get("write") else "只读"
        required = ",".join(item.get("required", [])) or "无"
        optional = sorted(
            set(item.get("arguments", [])) - set(item.get("required", []))
        )
        suffix = f"；可选={','.join(optional)}" if optional else ""
        rows.append(
            f"- {item.get('name')}：{item.get('title')} [{mode}]；"
            f"必填={required}{suffix}"
        )
    if len(rows) == 1:
        rows.append("- 当前没有可用能力")
    rows.append("参数格式：key=value,key=value；无参数使用 -")
    return "\n".join(rows)
