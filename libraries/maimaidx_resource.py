import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional

import aiofiles
import aiohttp
import py7zr

from .. import coverdir, log, maimaidir, platedir, ratingdir, static


RESOURCE_URL = "https://cloud.yuzuchan.moe/f/nXt6/Resource.7z"
RESOURCE_ARCHIVE_NAME = "Resource.7z"
EXPECTED_RESOURCE_PATHS = (
    maimaidir,
    coverdir,
    ratingdir,
    platedir,
    static / "ResourceHanRoundedCN-Bold.ttf",
    static / "ShangguMonoSC-Regular.otf",
    static / "Torus SemiBold.otf",
)


@dataclass
class ResourceInstallResult:
    copied_files: int = 0
    skipped_files: List[str] = field(default_factory=list)
    missing_paths: List[str] = field(default_factory=list)
    archive_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return not self.missing_paths


def _ensure_safe_archive_names(names: Iterable[str]) -> None:
    """Reject archive member names that could escape the extraction directory."""
    for raw_name in names:
        name = raw_name.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"资源包包含不安全路径：{raw_name}")
        if len(pure.parts) > 0 and ":" in pure.parts[0]:
            raise ValueError(f"资源包包含不安全路径：{raw_name}")


def _has_resource_markers(path: Path) -> bool:
    return any((path / marker).exists() for marker in ("mai", "ResourceHanRoundedCN-Bold.ttf", "echarts.min.js"))


def _find_static_source(extract_dir: Path) -> Path:
    """Find the static directory inside the extracted resource archive."""
    direct_static = extract_dir / "static"
    if direct_static.is_dir():
        return direct_static

    static_candidates = [p for p in extract_dir.rglob("static") if p.is_dir() and _has_resource_markers(p)]
    if static_candidates:
        static_candidates.sort(key=lambda p: len(p.parts))
        return static_candidates[0]

    if _has_resource_markers(extract_dir):
        return extract_dir

    nested_candidates = [p for p in extract_dir.iterdir() if p.is_dir() and _has_resource_markers(p)]
    if nested_candidates:
        return nested_candidates[0]

    raise FileNotFoundError("资源包中未找到 static 资源目录")


async def _download_file(url: str, target: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=3600, sock_connect=30, sock_read=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"下载资源失败，HTTP 状态码：{response.status}")
            async with aiofiles.open(target, "wb") as f:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    if chunk:
                        await f.write(chunk)


def _extract_7z(archive_path: Path, extract_dir: Path) -> None:
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        names = archive.getnames()
        _ensure_safe_archive_names(names)
        archive.extractall(path=extract_dir)


def _copy_resources(source_static: Path, target_static: Path) -> ResourceInstallResult:
    result = ResourceInstallResult()
    target_static.mkdir(parents=True, exist_ok=True)

    for src in source_static.rglob("*"):
        rel = src.relative_to(source_static)
        rel_key = rel.as_posix()
        dst = target_static / rel

        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if rel_key == "config.json":
            result.skipped_files.append(rel_key)
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        result.copied_files += 1

    result.missing_paths = [str(path.relative_to(static)) for path in EXPECTED_RESOURCE_PATHS if not path.exists()]
    return result


def refresh_resource_cache() -> None:
    """Refresh image assets cached in memory after resources are installed."""
    from .maimaidx_api_data import maiApi
    from .maimai_best_50 import ScoreBaseImage

    if maiApi.config.saveinmem:
        ScoreBaseImage._load_image()


async def install_maimai_resources(url: str = RESOURCE_URL) -> ResourceInstallResult:
    """Download Resource.7z, extract it, and copy resources into static/.

    The existing static/config.json is intentionally preserved because it contains
    local runtime configuration.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="maimaidx_resource_"))
    try:
        archive_path = tmp_root / RESOURCE_ARCHIVE_NAME
        extract_dir = tmp_root / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"开始下载舞萌资源：{url}")
        await _download_file(url, archive_path)
        log.info("舞萌资源下载完成，开始解压")

        await asyncio.to_thread(_extract_7z, archive_path, extract_dir)
        source_static = _find_static_source(extract_dir)
        result = await asyncio.to_thread(_copy_resources, source_static, static)
        result.archive_path = archive_path

        log.info(f"舞萌资源安装完成，复制文件数：{result.copied_files}")
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
