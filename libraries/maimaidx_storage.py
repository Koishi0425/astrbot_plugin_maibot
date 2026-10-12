"""Atomic JSON persistence with recoverable backups of corrupt files."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from astrbot.api import logger


class CorruptDataError(ValueError):
    """Stop mutations when an existing persistent store cannot be read."""


def preserve_corrupt(file: Path, raw: bytes | None = None) -> Path:
    """Keep a content-addressed copy of damaged JSON for administrator recovery.

    Args:
        file: Original data file, which remains untouched.
        raw: Already read bytes, or None to read the original file.

    Returns:
        The backup path.
    """
    raw = file.read_bytes() if raw is None else raw
    digest = hashlib.sha256(raw).hexdigest()[:12]
    backup = file.with_name(f"{file.name}.corrupt-{digest}.bak")
    try:
        with backup.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        pass
    logger.error("Corrupt plugin JSON preserved: %s", backup.name)
    return backup


def read_json(file: Path, expected_type: type | None = None) -> Any:
    """Read JSON and preserve corrupt bytes before reporting a recoverable error.

    Args:
        file: Persistent JSON path.
        expected_type: Optional required type of the JSON root.

    Returns:
        Decoded JSON data.

    Raises:
        CorruptDataError: JSON is invalid or has an unexpected root type.
        OSError: The original or backup file cannot be accessed.
    """
    raw = file.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
        if expected_type is not None and not isinstance(data, expected_type):
            raise ValueError("Unexpected JSON root type")
        return data
    except (ValueError, UnicodeError) as exc:
        backup = preserve_corrupt(file, raw)
        raise CorruptDataError(
            f"{file.name} 数据损坏，原文件已保留并备份为 {backup.name}；"
            "请管理员恢复数据后重试。"
        ) from exc


def write_json(file: Path, data: Any, *, protect_corrupt: bool = True) -> None:
    """Publish complete JSON through a same-directory temporary file.

    Args:
        file: Destination path.
        data: Serializable JSON payload.
        protect_corrupt: Stop replacing damaged user settings. Public caches may
            be regenerated from fresh validated responses after making a backup.

    Raises:
        CorruptDataError: An existing protected store is damaged.
        OSError: Writing, syncing or replacing the destination fails.
        ValueError: The payload cannot be serialized as valid JSON.
    """
    serialized = json.dumps(data, ensure_ascii=False, indent=4, allow_nan=False)
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.exists():
        try:
            read_json(file, type(data))
        except CorruptDataError:
            if protect_corrupt:
                raise
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=file.parent,
            prefix=f".{file.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
