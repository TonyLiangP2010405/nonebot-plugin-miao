"""原神角色攻略图数据源。

行为参考 Yunzai-genshin 的 strategy 模块，网络与文件操作改为异步实现。
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger

API_URL = "https://bbs-api.mihoyo.com/post/wapi/getPostFullInCollection"
IMAGE_PROCESS = "x-oss-process=image/resize,s_1200/quality,q_90/auto-orient,0/interlace,1/format,jpg"

SOURCE_NAMES = (
    "西风驿站",
    "原神观测枢",
    "派蒙喵喵屋",
    "OH是姜姜呀",
    "曉K",
    "坤易",
    "婧枫赛赛（角色配队一图流）",
)

COLLECTION_IDS: dict[int, tuple[int, ...]] = {
    1: (2319292, 2319293, 2319295, 2319296, 2319299, 2319294, 2319298, 642956),
    2: (813033,),
    3: (341284,),
    4: (341523,),
    5: (1582613,),
    6: (22148,),
    7: (1812949,),
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nonebot-plugin-miao/1.0)",
    "Referer": "https://www.miyoushe.com/ys/",
}
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StrategyDataError(ValueError):
    """攻略数据源不可用。"""


def source_name(source: int) -> str:
    """返回来源名称，编号非法时抛出用户可读异常。"""
    if source not in COLLECTION_IDS:
        raise ValueError("攻略来源必须是 1-7 的数字")
    return SOURCE_NAMES[source - 1]


def _cache_root() -> Path:
    """攻略图片缓存目录，测试中可替换。"""
    from nonebot_plugin_localstore import get_plugin_cache_dir

    return Path(get_plugin_cache_dir()) / "strategy"


def cache_path(role_name: str, source: int) -> Path:
    """生成跨平台安全的攻略图片缓存路径。"""
    safe_name = _INVALID_FILENAME.sub("_", role_name).strip(". ") or "unknown"
    return _cache_root() / str(source) / f"{safe_name}.jpg"


async def _read_cache(path: Path) -> bytes | None:
    def read() -> bytes | None:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return data or None

    return await asyncio.to_thread(read)


async def _write_cache(path: Path, data: bytes) -> None:
    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    await asyncio.to_thread(write)


async def _fetch_collection(client: httpx.AsyncClient, collection_id: int) -> dict[str, Any]:
    response = await client.get(
        API_URL,
        params={"gids": 2, "order_type": 2, "collection_id": collection_id},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("retcode") != 0:
        message = payload.get("message", "未知错误") if isinstance(payload, dict) else "响应格式错误"
        raise StrategyDataError(f"攻略数据源返回异常：{message}")
    return payload


async def _fetch_payloads(client: httpx.AsyncClient, source: int) -> list[dict[str, Any]]:
    ids = COLLECTION_IDS.get(source)
    if not ids:
        raise ValueError("攻略来源必须是 1-7 的数字")

    results = await asyncio.gather(*(_fetch_collection(client, item) for item in ids), return_exceptions=True)
    payloads: list[dict[str, Any]] = []
    for collection_id, result in zip(ids, results):
        if isinstance(result, BaseException):
            logger.warning(f"[miao-strategy] 合集 {collection_id} 获取失败: {result}")
        else:
            payloads.append(result)
    if not payloads:
        raise StrategyDataError("攻略数据源暂时不可用，请稍后再试")
    return payloads


def _image_size(image: dict[str, Any]) -> int:
    try:
        return int(image.get("size") or 0)
    except (TypeError, ValueError):
        try:
            return int(image.get("width") or 0) * int(image.get("height") or 0)
        except (TypeError, ValueError):
            return 0


def _largest_image_url(images: list[dict[str, Any]]) -> str | None:
    valid = [image for image in images if isinstance(image, dict) and image.get("url")]
    if not valid:
        return None
    return str(max(valid, key=_image_size)["url"])


def _source_four_image_url(content: str, role_name: str, images: list[dict[str, Any]]) -> str | None:
    """OH是姜姜呀合集在正文中标记角色对应图片，按 image_id 精确取图。"""
    position = content.find(role_name)
    if position < 0:
        return None
    fragment = content[position:]
    match = re.search(r'image\\?"\s*:\s*\\?"([^"\\]+)', fragment)
    if not match:
        return None
    image_id = match.group(1)
    for image in images:
        if isinstance(image, dict) and str(image.get("image_id") or "") == image_id and image.get("url"):
            return str(image["url"])
    return None


def find_strategy_image_url(role_name: str, source: int, payloads: list[dict[str, Any]]) -> str | None:
    """从米游社合集响应中找到角色攻略主图。"""
    for payload in payloads:
        posts = ((payload.get("data") or {}).get("posts") or []) if isinstance(payload, dict) else []
        for item in posts:
            if not isinstance(item, dict):
                continue
            post = item.get("post") or {}
            images = item.get("image_list") or []
            if not isinstance(post, dict) or not isinstance(images, list):
                continue
            subject = str(post.get("subject") or "")
            content = str(post.get("structured_content") or "")

            if source == 4 and role_name in content:
                url = _source_four_image_url(content, role_name, images)
                if url:
                    return url
            if role_name in subject:
                url = _largest_image_url(images)
                if url:
                    return url
    return None


def _optimized_image_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{IMAGE_PROCESS}"


def _looks_like_image(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    data = response.content
    return content_type.startswith("image/") or data.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF", b"GIF8"))


async def _download_image(client: httpx.AsyncClient, url: str) -> bytes:
    """优先下载压缩后的攻略图，图床不支持参数时回退原图。"""
    last_error: Exception | None = None
    for candidate in (_optimized_image_url(url), url):
        try:
            response = await client.get(candidate)
            response.raise_for_status()
            if response.content and _looks_like_image(response):
                return response.content
            last_error = StrategyDataError("攻略图响应格式错误")
        except (httpx.HTTPError, StrategyDataError) as error:
            last_error = error
    raise StrategyDataError("攻略图片下载失败，请稍后再试") from last_error


async def _fetch_remote(role_name: str, source: int, client: httpx.AsyncClient) -> bytes | None:
    payloads = await _fetch_payloads(client, source)
    image_url = find_strategy_image_url(role_name, source, payloads)
    if not image_url:
        return None
    return await _download_image(client, image_url)


async def fetch_strategy_image(
    role_name: str,
    source: int,
    *,
    refresh: bool = False,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    """获取角色攻略图；默认优先缓存，refresh=True 时强制刷新。"""
    source_name(source)
    path = cache_path(role_name, source)
    if not refresh:
        cached = await _read_cache(path)
        if cached:
            return cached

    if client is None:
        timeout = httpx.Timeout(30, connect=10)
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=True) as own_client:
            data = await _fetch_remote(role_name, source, own_client)
    else:
        data = await _fetch_remote(role_name, source, client)

    if data:
        await _write_cache(path, data)
    return data
