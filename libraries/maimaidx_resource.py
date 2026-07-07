import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Union

import aiofiles
import aiohttp
import py7zr

from .. import (
    Root,
    coverdir,
    log,
    maimaidir,
    platedir,
    plate_versiondir,
    ratingdir,
    shougoudir,
    static,
    themepicdir,
)


RESOURCE_ARCHIVE_NAME = "Resource.7z"
RATING_DIGIT_NAMES = tuple(f"UI_NUM_Drating_{i}.png" for i in range(10))
FONT_NAMES = (
    "ResourceHanRoundedCN-Bold.ttf",
    "ShangguMonoSC-Regular.otf",
    "Torus SemiBold.otf",
)
EXPECTED_RESOURCE_PATHS = (
    maimaidir,
    themepicdir,
    coverdir,
    ratingdir,
    platedir,
    plate_versiondir,
    shougoudir,
    themepicdir / "b50.png",
    themepicdir / "title.png",
    themepicdir / "title_lengthen.png",
    themepicdir / "chart_info.png",
    themepicdir / "play_info.png",
    themepicdir / "logo.png",
    themepicdir / "design.png",
    maimaidir / "complete.png",
    maimaidir / "complete_1.png",
    maimaidir / "unfinished_1.png",
    maimaidir / "complete_2.png",
    maimaidir / "unfinished_2.png",
    maimaidir / "UI_Icon_509506.png",
    maimaidir / "UI_Plate_550101.png",
    *(static / "font" / name for name in FONT_NAMES),
)


class ResourceInstallError(RuntimeError):
    pass


@dataclass
class ResourceStatus:
    missing_paths: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_paths and not self.warnings


@dataclass
class ResourceInstallResult:
    copied_files: int = 0
    skipped_files: List[str] = field(default_factory=list)
    missing_paths: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source_paths: List[str] = field(default_factory=list)
    used_url: Optional[str] = None
    archive_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return not self.missing_paths


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Root))
    except ValueError:
        return str(path)


def _resource_path(path: Path) -> str:
    try:
        return str(path.relative_to(static)).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_local_path(value: Optional[Union[str, Path]]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Root / path
    return path


def _ensure_safe_archive_names(names: Iterable[str]) -> None:
    """Reject archive member names that could escape the extraction directory."""
    for raw_name in names:
        name = raw_name.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise ResourceInstallError(f"资源包包含不安全路径：{raw_name}")
        if pure.parts and ":" in pure.parts[0]:
            raise ResourceInstallError(f"资源包包含不安全路径：{raw_name}")


def _has_static_markers(path: Path) -> bool:
    return any((path / marker).exists() for marker in ("mai", "font", "echarts.min.js"))


def _is_rating_digit_update(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any((path / name).is_file() for name in RATING_DIGIT_NAMES)


def _find_rating_digit_update(path: Path) -> Optional[Path]:
    if _is_rating_digit_update(path):
        return path
    candidates = [p for p in path.rglob("*") if p.is_dir() and _is_rating_digit_update(p)]
    candidates.sort(key=lambda p: len(p.parts))
    return candidates[0] if candidates else None


def _find_static_source(extract_dir: Path) -> Path:
    """Find the static directory inside a full resource package."""
    direct_static = extract_dir / "static"
    if direct_static.is_dir() and _has_static_markers(direct_static):
        return direct_static

    static_candidates = [
        p for p in extract_dir.rglob("static") if p.is_dir() and _has_static_markers(p)
    ]
    if static_candidates:
        static_candidates.sort(key=lambda p: len(p.parts))
        return static_candidates[0]

    if _has_static_markers(extract_dir):
        return extract_dir

    nested_candidates = [
        p for p in extract_dir.iterdir() if p.is_dir() and _has_static_markers(p)
    ]
    if nested_candidates:
        nested_candidates.sort(key=lambda p: len(p.parts))
        return nested_candidates[0]

    raise ResourceInstallError("资源包结构不匹配：未找到 static 资源目录")


def _is_supported_full_static(source_static: Path) -> bool:
    required = (
        source_static / "mai" / "pic" / "prism_plus" / "b50.png",
        source_static / "mai" / "pic" / "prism_plus" / "title.png",
        source_static / "mai" / "pic" / "prism_plus" / "title_lengthen.png",
    )
    return all(path.is_file() for path in required)


def _copy_file(src: Path, dst: Path, result: ResourceInstallResult) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    result.copied_files += 1


def _copy_tree(source: Path, target: Path, result: ResourceInstallResult) -> None:
    target.mkdir(parents=True, exist_ok=True)

    for src in source.rglob("*"):
        rel = src.relative_to(source)
        rel_key = rel.as_posix()
        dst = target / rel

        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if src.name.lower() == "config.json":
            result.skipped_files.append(rel_key)
            continue

        _copy_file(src, dst, result)


def _ensure_current_layout_dirs() -> None:
    ratingdir.mkdir(parents=True, exist_ok=True)
    platedir.mkdir(parents=True, exist_ok=True)
    plate_versiondir.mkdir(parents=True, exist_ok=True)
    shougoudir.mkdir(parents=True, exist_ok=True)


def _copy_full_static(source_static: Path, result: ResourceInstallResult) -> None:
    _copy_tree(source_static, static, result)
    _ensure_current_layout_dirs()


def _copy_rating_digit_update(source_dir: Path, result: ResourceInstallResult) -> None:
    maimaidir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in RATING_DIGIT_NAMES:
        src = source_dir / name
        if src.is_file():
            _copy_file(src, maimaidir / name, result)
            copied += 1
    if copied == 0:
        raise ResourceInstallError("资源包结构不匹配：增量包中未找到 rating 数字素材")


def _install_resource_dir(source: Path, result: ResourceInstallResult) -> None:
    rating_update = _find_rating_digit_update(source)
    if rating_update and not _has_static_markers(source) and not (source / "static").is_dir():
        _copy_rating_digit_update(rating_update, result)
        return

    try:
        source_static = _find_static_source(source)
    except ResourceInstallError:
        if rating_update:
            _copy_rating_digit_update(rating_update, result)
            return
        raise
    if not _is_supported_full_static(source_static):
        raise ResourceInstallError("资源结构不匹配：缺少新版 prism_plus 主题素材")
    _copy_full_static(source_static, result)


def _extract_7z(archive_path: Path, extract_dir: Path) -> None:
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        names = archive.getnames()
        _ensure_safe_archive_names(names)
        archive.extractall(path=extract_dir)


def _install_archive(archive_path: Path, result: ResourceInstallResult) -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="maimaidx_resource_extract_"))
    try:
        extract_dir = tmp_root / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _extract_7z(archive_path, extract_dir)
        _install_resource_dir(extract_dir, result)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _looks_like_update(path: Path) -> bool:
    return "update" in path.name.lower() or _find_rating_digit_update(path) is not None


def _deduplicate_candidates(candidates: List[Path]) -> List[Path]:
    dirs_by_name = {p.name.lower(): p for p in candidates if p.is_dir()}
    result = []
    for path in candidates:
        if path.is_file() and path.suffix.lower() == ".7z":
            if path.stem.lower() in dirs_by_name:
                continue
        result.append(path)
    return result


def _find_local_candidates(path: Path) -> List[Path]:
    if path.is_file():
        if path.suffix.lower() != ".7z":
            raise ResourceInstallError(f"本地资源不可用：{_display_path(path)} 不是 .7z 资源包")
        return [path]

    if not path.is_dir():
        return []

    if (
        _has_static_markers(path)
        or (path / "static").is_dir()
        or _is_rating_digit_update(path)
        or ("update" in path.name.lower() and _find_rating_digit_update(path))
    ):
        return [path]

    candidates = [
        p for p in path.iterdir()
        if p.is_dir() or (p.is_file() and p.suffix.lower() == ".7z")
    ]
    candidates = _deduplicate_candidates(candidates)
    valid_candidates = []
    for candidate in candidates:
        if candidate.is_file():
            valid_candidates.append(candidate)
        else:
            try:
                source_static = _find_static_source(candidate)
                if _is_supported_full_static(source_static):
                    valid_candidates.append(candidate)
                    continue
            except ResourceInstallError:
                pass
            if _is_rating_digit_update(candidate) or (
                "update" in candidate.name.lower() and _find_rating_digit_update(candidate)
            ):
                valid_candidates.append(candidate)

    valid_candidates.sort(key=lambda p: (1 if _looks_like_update(p) else 0, p.name.lower()))
    return valid_candidates


async def _download_file(url: str, target: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=3600, sock_connect=30, sock_read=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise ResourceInstallError(f"下载资源失败，HTTP 状态码：{response.status}")
            async with aiofiles.open(target, "wb") as f:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    if chunk:
                        await f.write(chunk)


def check_resource_status() -> ResourceStatus:
    status = ResourceStatus()

    for path in EXPECTED_RESOURCE_PATHS:
        if not path.exists():
            status.missing_paths.append(_resource_path(path))

    missing_digits = [name for name in RATING_DIGIT_NAMES if not (maimaidir / name).exists()]
    if missing_digits:
        status.warnings.append(
            "缺少 rating 数字素材："
            + ", ".join(missing_digits[:3])
            + (" ..." if len(missing_digits) > 3 else "")
        )

    return status


def format_missing_resource_error(error: FileNotFoundError) -> str:
    filename = getattr(error, "filename", None)
    path = Path(filename) if filename else None
    resource = _resource_path(path) if path else str(error)
    return (
        f"舞萌静态资源缺少关键文件：{resource}\n"
        "请联系Bot管理员执行「更新maimai数据」，并确认 static 资源已更新到当前街机版本。"
    )


async def install_maimai_resources(
    local_path: Optional[Union[str, Path]] = "",
    url: Optional[str] = "",
) -> ResourceInstallResult:
    """Install resources into static/ from a local package/directory first, then optional URL."""
    result = ResourceInstallResult()
    resolved_local = _resolve_local_path(local_path)

    if resolved_local and resolved_local.exists():
        candidates = _find_local_candidates(resolved_local)
        if not candidates:
            raise ResourceInstallError(f"本地资源不可用：{_display_path(resolved_local)} 中未找到可识别的资源包")

        for candidate in candidates:
            result.source_paths.append(_display_path(candidate))
            log.info(f"开始安装本地舞萌资源：{candidate}")
            if candidate.is_file():
                await asyncio.to_thread(_install_archive, candidate, result)
            else:
                await asyncio.to_thread(_install_resource_dir, candidate, result)

        status = check_resource_status()
        result.missing_paths = status.missing_paths
        result.warnings = status.warnings
        return result

    if resolved_local:
        log.warning(f"本地资源路径不存在：{resolved_local}")

    url = (url or "").strip()
    if not url:
        if resolved_local:
            raise ResourceInstallError("未找到本地资源，且未在 WebUI 配置资源 URL")
        raise ResourceInstallError("未配置本地资源路径或资源 URL，无法更新 static 资源")

    tmp_root = Path(tempfile.mkdtemp(prefix="maimaidx_resource_download_"))
    try:
        archive_path = tmp_root / RESOURCE_ARCHIVE_NAME
        log.info(f"开始下载舞萌资源：{url}")
        await _download_file(url, archive_path)
        result.used_url = url
        result.archive_path = archive_path
        await asyncio.to_thread(_install_archive, archive_path, result)
        status = check_resource_status()
        result.missing_paths = status.missing_paths
        result.warnings = status.warnings
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def refresh_resource_cache() -> None:
    """Refresh image assets cached in memory after resources are installed."""
    from .maimaidx_api_data import maiApi
    from .maimai_best_50 import ScoreBaseImage

    if maiApi.config.saveinmem:
        ScoreBaseImage._load_image()
