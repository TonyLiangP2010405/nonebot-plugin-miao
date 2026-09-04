"""原神、星铁与绝区零角色攻略图数据源。

行为参考 Yunzai-genshin 的 strategy 模块，网络与文件操作改为异步实现。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger

API_URLS = (
    "https://bbs-api.miyoushe.com/post/wapi/getPostFullInCollection",
    "https://bbs-api.mihoyo.com/post/wapi/getPostFullInCollection",
)
POST_API_URLS = (
    "https://bbs-api.miyoushe.com/post/wapi/getPostFull",
    "https://bbs-api.mihoyo.com/post/wapi/getPostFull",
)
# 保留旧的单地址常量，方便外部调用方和测试读取；请求时会按 API_URLS 自动回退。
API_URL = API_URLS[0]
IMAGE_PROCESS = "x-oss-process=image/resize,s_1200/quality,q_90/auto-orient,0/interlace,1/format,jpg"

GAME_GIDS = {"gs": 2, "sr": 6, "zzz": 8}
GAME_NAMES = {"gs": "原神", "sr": "星铁", "zzz": "绝区零"}

SOURCE_NAMES_BY_GAME: dict[str, tuple[str, ...]] = {
    "gs": (
        "西风驿站",
        "原神观测枢",
        "派蒙喵喵屋",
        "OH是姜姜呀",
        "曉K",
        "坤易",
        "婧枫赛赛（角色配队一图流）",
    ),
    "sr": (
        "列车广播（车站指南）",
        "牧牧Muu（崩铁角色文图攻略）",
        "丶ATRI丶（星铁角色攻略）",
    ),
    "zzz": (
        "绳么东西BROADCAST（角色攻略合集）",
        "烟雨如诗（绝区零角色攻略）",
        "丶ATRI丶（绝区零一图流）",
        "小橙子阿（绝区零角色攻略）",
    ),
}

COLLECTION_IDS_BY_GAME: dict[str, dict[int, tuple[int, ...]]] = {
    "gs": {
        1: (2319292, 2319293, 2319295, 2319296, 2319299, 2319294, 2319298, 642956),
        2: (813033,),
        3: (341284,),
        4: (341523,),
        5: (1582613,),
        6: (22148,),
        7: (1812949,),
    },
    "sr": {
        1: (917513,),
        2: (3241248,),
        3: (3388828,),
    },
    "zzz": {
        1: (3156883,),
        2: (3108686,),
        3: (3388948,),
        4: (3032407,),
    },
}

# 原有公开常量继续表示原神来源，避免破坏已安装版本的兼容性。
SOURCE_NAMES = SOURCE_NAMES_BY_GAME["gs"]
COLLECTION_IDS = COLLECTION_IDS_BY_GAME["gs"]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nonebot-plugin-miao/1.0)",
    "Referer": "https://www.miyoushe.com/",
}
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StrategyDataError(ValueError):
    """攻略数据源不可用。"""


def _check_game(game: str) -> None:
    if game not in GAME_GIDS:
        raise ValueError(f"非法攻略游戏标识：{game!r}")


def source_count(game: str = "gs") -> int:
    """返回指定游戏可用的攻略来源数量。"""
    _check_game(game)
    return len(SOURCE_NAMES_BY_GAME[game])


def source_name(source: int, game: str = "gs") -> str:
    """返回来源名称，编号非法时抛出用户可读异常。"""
    _check_game(game)
    if source not in COLLECTION_IDS_BY_GAME[game]:
        raise ValueError(f"{GAME_NAMES[game]}攻略来源必须是 1-{source_count(game)} 的数字")
    return SOURCE_NAMES_BY_GAME[game][source - 1]


def _cache_root() -> Path:
    """攻略图片缓存目录，测试中可替换。"""
    from nonebot_plugin_localstore import get_plugin_cache_dir

    return Path(get_plugin_cache_dir()) / "strategy"


def cache_path(role_name: str, source: int, game: str = "gs") -> Path:
    """生成跨平台安全的攻略图片缓存路径。"""
    _check_game(game)
    safe_name = _INVALID_FILENAME.sub("_", role_name).strip(". ") or "unknown"
    # 原神沿用旧目录；星铁与绝区零升级缓存版本，自动淘汰 0.1.12 错存的合集封面。
    base = _cache_root() if game == "gs" else _cache_root() / "guide-v2" / game
    return base / str(source) / f"{safe_name}.jpg"


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


async def _fetch_collection(client: httpx.AsyncClient, collection_id: int, game: str = "gs") -> dict[str, Any]:
    """获取一个米游社合集；新域名失败时自动尝试旧域名。"""
    _check_game(game)
    last_error: Exception | None = None
    for api_url in API_URLS:
        try:
            response = await client.get(
                api_url,
                params={"gids": GAME_GIDS[game], "order_type": 2, "collection_id": collection_id},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("retcode") != 0:
                message = payload.get("message", "未知错误") if isinstance(payload, dict) else "响应格式错误"
                raise StrategyDataError(f"攻略数据源返回异常：{message}")
            return payload
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
    raise StrategyDataError("攻略数据源请求失败") from last_error


async def _fetch_post(client: httpx.AsyncClient, post_id: int, game: str) -> dict[str, Any]:
    """获取米游社单篇攻略正文；新域名失败时自动尝试旧域名。"""
    _check_game(game)
    last_error: Exception | None = None
    for api_url in POST_API_URLS:
        try:
            response = await client.get(
                api_url,
                params={"gids": GAME_GIDS[game], "post_id": post_id, "read": 1},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("retcode") != 0:
                message = payload.get("message", "未知错误") if isinstance(payload, dict) else "响应格式错误"
                raise StrategyDataError(f"攻略正文返回异常：{message}")
            return payload
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
    raise StrategyDataError("攻略正文请求失败") from last_error


async def _fetch_payloads(client: httpx.AsyncClient, source: int, game: str = "gs") -> list[dict[str, Any]]:
    _check_game(game)
    ids = COLLECTION_IDS_BY_GAME[game].get(source)
    if not ids:
        raise ValueError(f"{GAME_NAMES[game]}攻略来源必须是 1-{source_count(game)} 的数字")

    results = await asyncio.gather(*(_fetch_collection(client, item, game) for item in ids), return_exceptions=True)
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


def _normalized_text(value: str) -> str:
    """忽略空白、连接符和中英文标点进行角色名匹配。"""
    return re.sub(r"[\W_]+", "", str(value).casefold(), flags=re.UNICODE)


def _role_search_keys(role_name: str) -> tuple[str, ...]:
    normalized = _normalized_text(role_name)
    keys = [normalized]
    # 星铁元数据用 Pro 表示加强形态，米游社标题通常只写角色名或“加强”。
    if normalized.endswith("pro") and len(normalized) > 3:
        keys.append(normalized[:-3])
    return tuple(key for key in keys if key)


def _structured_operations(content: str) -> list[dict[str, Any]]:
    """解析米游社富文本操作列表，异常内容按空列表处理。"""
    try:
        operations = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(operations, list):
        return []
    return [operation for operation in operations if isinstance(operation, dict)]


def find_strategy_article_ids(role_name: str, payloads: list[dict[str, Any]]) -> list[int]:
    """从角色合集里提取对应攻略正文链接，并以合集帖自身作为最后回退。"""
    search_keys = _role_search_keys(role_name)
    linked_ids: list[int] = []
    own_ids: list[int] = []
    for payload in payloads:
        posts = ((payload.get("data") or {}).get("posts") or []) if isinstance(payload, dict) else []
        for item in posts:
            if not isinstance(item, dict):
                continue
            post = item.get("post") or {}
            if not isinstance(post, dict):
                continue
            subject = _normalized_text(str(post.get("subject") or ""))
            if not any(key in subject for key in search_keys):
                continue
            for operation in _structured_operations(str(post.get("structured_content") or "")):
                inserted = operation.get("insert")
                attributes = operation.get("attributes") or {}
                if not isinstance(inserted, str) or not isinstance(attributes, dict):
                    continue
                if not any(key in _normalized_text(inserted) for key in search_keys):
                    continue
                match = re.search(r"/article/(\d+)", str(attributes.get("link") or ""))
                if match:
                    linked_ids.append(int(match.group(1)))
            try:
                own_ids.append(int(post.get("post_id")))
            except (TypeError, ValueError):
                pass
    return list(dict.fromkeys((*linked_ids, *own_ids)))


def find_post_guide_image_url(payload: dict[str, Any]) -> str | None:
    """从单篇攻略正文中排除封面，选择信息量最大的正文攻略图。"""
    wrapper = ((payload.get("data") or {}).get("post") or {}) if isinstance(payload, dict) else {}
    if not isinstance(wrapper, dict):
        return None
    images = wrapper.get("image_list") or []
    cover = wrapper.get("cover") or {}
    if not isinstance(images, list) or not isinstance(cover, dict):
        return None
    cover_id = str(cover.get("image_id") or "")
    cover_url = str(cover.get("url") or "")
    candidates: list[dict[str, Any]] = []
    for image in images:
        if not isinstance(image, dict) or not image.get("url"):
            continue
        if cover_id and str(image.get("image_id") or "") == cover_id:
            continue
        if cover_url and str(image.get("url") or "") == cover_url:
            continue
        candidates.append(image)
    if not candidates:
        return None
    return str(max(candidates, key=_image_size)["url"])


def find_strategy_image_url(
    role_name: str,
    source: int,
    payloads: list[dict[str, Any]],
    game: str = "gs",
) -> str | None:
    """从原神米游社合集响应中找到角色攻略主图。"""
    _check_game(game)
    if game != "gs":
        return None
    search_keys = _role_search_keys(role_name)
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

            if game == "gs" and source == 4 and role_name in content:
                url = _source_four_image_url(content, role_name, images)
                if url:
                    return url
            normalized_subject = _normalized_text(subject)
            if any(key in normalized_subject for key in search_keys):
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


async def _fetch_remote(role_name: str, source: int, game: str, client: httpx.AsyncClient) -> bytes | None:
    payloads = await _fetch_payloads(client, source, game)
    if game != "gs":
        article_ids = find_strategy_article_ids(role_name, payloads)
        for article_id in article_ids:
            try:
                post_payload = await _fetch_post(client, article_id, game)
                image_url = find_post_guide_image_url(post_payload)
                if image_url:
                    return await _download_image(client, image_url)
            except StrategyDataError as error:
                logger.warning(f"[miao-strategy] 攻略正文 {article_id} 获取失败: {error}")
        return None

    image_url = find_strategy_image_url(role_name, source, payloads, game)
    if not image_url:
        return None
    return await _download_image(client, image_url)


async def fetch_strategy_image(
    role_name: str,
    source: int,
    *,
    game: str = "gs",
    refresh: bool = False,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    """获取角色攻略图；默认优先缓存，refresh=True 时强制刷新。"""
    source_name(source, game)
    path = cache_path(role_name, source, game)
    if not refresh:
        cached = await _read_cache(path)
        if cached:
            return cached

    if client is None:
        timeout = httpx.Timeout(30, connect=10)
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=True) as own_client:
            data = await _fetch_remote(role_name, source, game, own_client)
    else:
        data = await _fetch_remote(role_name, source, game, client)

    if data:
        await _write_cache(path, data)
    return data
