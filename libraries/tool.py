import asyncio
import time
from pathlib import Path
from typing import Any, Union

from playwright.async_api import async_playwright

from .. import SNAPSHOT_JS
from .maimaidx_storage import read_json, write_json


def qqhash(qq):
    """
    计算 QQ 号的哈希值

    Args:
        qq: QQ 号，可以是整数或字符串

    Returns:
        哈希值（整数）
    """
    # 确保 qq 是整数类型
    try:
        if isinstance(qq, str):
            qq = int(qq)
        elif not isinstance(qq, int):
            qq = int(qq)
    except (ValueError, TypeError):
        qq = 0

    days = (
        int(time.strftime("%d", time.localtime(time.time())))
        + 31 * int(time.strftime("%m", time.localtime(time.time())))
        + 77
    )
    return (days * qq) >> 8


async def openfile(file: Path) -> Union[dict, list]:
    """Read JSON off the event loop, preserving corrupt data for recovery.

    Args:
        file: Persistent JSON path.

    Returns:
        Decoded JSON data.
    """
    return await asyncio.to_thread(read_json, file)


async def writefile(file: Path, data: Any, *, protect_corrupt: bool = True) -> bool:
    """Atomically save a complete JSON payload off the event loop.

    Args:
        file: Persistent JSON path.
        data: Serializable payload.
        protect_corrupt: Prevent overwriting damaged user settings.

    Returns:
        True after the replacement succeeds.
    """
    await asyncio.to_thread(write_json, file, data, protect_corrupt=protect_corrupt)
    return True


async def run_chrome_to_base64(html_file: Path) -> str:
    """Capture a request-specific ECharts document after rendering completes.

    Args:
        html_file: Unique temporary HTML file owned by the calling request.

    Returns:
        A base64 image message URI.

    Raises:
        OSError: The browser did not return an image.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(java_script_enabled=True)
            await page.goto(html_file.resolve().as_uri())
            await page.wait_for_function(
                "typeof echarts !== 'undefined' && "
                "document.querySelector('div[_echarts_instance_]') && "
                "echarts.getInstanceByDom(document.querySelector('div[_echarts_instance_]'))",
                timeout=15_000,
            )
            content: str = await page.evaluate(SNAPSHOT_JS)
        finally:
            await browser.close()
    content_array = content.split(",")
    if len(content_array) != 2:
        raise OSError(content_array)

    return "base64://" + content_array[-1]
