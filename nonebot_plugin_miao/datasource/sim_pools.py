"""三游戏国服模拟卡池：官方公开清单/详情 → 校验 → 原子缓存。

不依赖随包角色元数据；新角色、四星陪跑和常驻物品直接来自对应卡池详情。
时间均按 UTC+8 解析。缓存失败不覆盖旧文件，过期/未开放的池不参与模拟。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger

from ..core import store

CN_TZ = timezone(timedelta(hours=8))
GAME_NAMES = {"gs": "原神", "sr": "星铁", "zzz": "绝区零"}
BASE_URLS = {
    "gs": "https://operation-webstatic.mihoyo.com/gacha_info/hk4e/cn_gf01",
    "sr": "https://operation-webstatic.mihoyo.com/gacha_info/hkrpg/prod_gf_cn",
    "zzz": "https://operation-webstatic.mihoyo.com/gacha_info/nap/prod_gf_cn",
}
ICON_META_URLS = {
    "sr": (
        "https://cdn.jsdelivr.net/gh/EnkaNetwork/API-docs@master/store/hsr/avatars.json",
        "https://cdn.jsdelivr.net/gh/EnkaNetwork/API-docs@master/store/hsr/weapons.json",
        "https://cdn.jsdelivr.net/gh/EnkaNetwork/API-docs@master/store/hsr/hsr.json",
    ),
    "zzz": (
        "https://cdn.jsdelivr.net/gh/EnkaNetwork/API-docs@master/store/zzz/avatars.json",
        "https://cdn.jsdelivr.net/gh/EnkaNetwork/API-docs@master/store/zzz/weapons.json",
    ),
}
ENKA_UI_BASE = "https://enka.network"
SNAPSHOT_SCHEMA = 2
# (抽取类型, 保底分组)：联动跃迁与普通跃迁独立，复刻池与普通活动池共享。
POOL_TYPES = {
    "gs": {200: ("permanent", "permanent"), 301: ("role", "role"), 400: ("role", "role"), 302: ("weapon", "weapon")},
    "sr": {
        1: ("permanent", "permanent"),
        11: ("role", "role"),
        12: ("weapon", "weapon"),
        21: ("role", "collab_role"),
        22: ("weapon", "collab_weapon"),
    },
    "zzz": {
        1001: ("permanent", "permanent"),
        **{k: ("role", "role") for k in (2001, 2002, 2011, 2012)},
        **{k: ("weapon", "weapon") for k in (3001, 3002, 3011, 3012)},
    },
}
_LOCKS = {game: asyncio.Lock() for game in GAME_NAMES}
_RETRY_AFTER: dict[str, float] = {}


class PoolError(ValueError):
    """可直接回复给用户的卡池错误。"""


def check_game(game: str) -> None:
    if game not in GAME_NAMES:
        raise PoolError("模拟抽卡仅支持原神、星铁和绝区零")


def timestamp(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ).timestamp()


def cache_path(game: str) -> Path:
    check_game(game)
    return store._data_dir() / "sim_pools" / f"{game}.json"


def read_snapshot(game: str) -> dict:
    data = store.load_json(cache_path(game), {})
    # 继续读取第一版缓存用于同步失败时兜底；_due 会要求它尽快升级。
    return data if isinstance(data, dict) and data.get("schema") in (1, SNAPSHOT_SCHEMA) else {}


def active_pools(snapshot: dict, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    return [p for p in snapshot.get("pools", []) if p["start"] <= now <= p["end"]]


def rate(value: Any) -> int:
    """官方百分数字符串转万分比，拒绝异常概率。"""
    result = round(float(str(value).strip().removesuffix("%")) * 100)
    if not 0 <= result <= 10000:
        raise PoolError("官方卡池概率数据异常")
    return result


def parse_pool(game: str, row: dict, detail: dict) -> dict:
    kind, group = POOL_TYPES[game][int(row["gacha_type"])]
    if int(detail["gacha_type"]) != int(row["gacha_type"]):
        raise PoolError("官方卡池清单与详情不一致")
    items: dict[str, dict] = {}
    groups: dict[int, list[str]] = {3: [], 4: [], 5: []}
    up: dict[int, list[str]] = {4: [], 5: []}
    # 星铁/绝区零卡池的常规物品表常把 image_url 留空，但同一响应里的
    # UP、自选列表可能携带图片。先收集起来，供常规物品回填。
    detail_images_by_id: dict[int, str] = {}
    detail_images_by_name: dict[str, str] = {}
    for value in detail.values():
        if not isinstance(value, list):
            continue
        for raw in value:
            if not isinstance(raw, dict):
                continue
            image = str(raw.get("item_img") or raw.get("image_url") or "")
            if not image.startswith("https://"):
                continue
            item_id = int(raw.get("origin_item_id") or 0)
            name = str(raw.get("item_name") or "").strip()
            if item_id:
                detail_images_by_id[item_id] = image
            if name:
                detail_images_by_name[name] = image

    def add(raw: dict, star: int, item_type: str, is_up: bool = False) -> None:
        name = str(raw.get("item_name") or "").strip()
        if not name:
            raise PoolError("官方卡池物品名称缺失")
        previous = items.get(name, {})
        item_id = int(raw.get("origin_item_id") or previous.get("itemId") or 0)
        items[name] = {
            "name": name,
            "star": star,
            "type": item_type,
            "element": raw.get("item_attr") or previous.get("element", ""),
            "itemId": item_id,
            "imgFile": (
                raw.get("item_img")
                or raw.get("image_url")
                or detail_images_by_id.get(item_id)
                or detail_images_by_name.get(name)
                or previous.get("imgFile", "")
            ),
        }
        if name not in groups[star]:
            groups[star].append(name)
        if is_up and name not in up[star]:
            up[star].append(name)

    if game == "gs":
        for star in (3, 4, 5):
            for item in detail.get(f"r{star}_prob_list") or []:
                add(item, star, "role" if item["item_type"] == "角色" else "weapon", bool(item.get("is_up")))
        for star in (4, 5):
            for item in detail.get(f"r{star}_up_items") or []:
                add(item, star, "role" if item["item_type"] == "角色" else "weapon", True)
        rate5, rate4 = rate(detail["r5_prob"]), rate(detail["r4_prob"])
        up5, up4 = rate(detail["r5_up_prob"]), rate(detail["r4_up_prob"])
    else:
        for category, item_type in (("avatar", "role"), ("light_cone", "weapon")):
            for star in (3, 4, 5):
                for item in detail.get(f"items_{category}_star_{star}") or []:
                    add(item, star, item_type, bool(item.get("is_up")))
        for star in (4, 5):
            for item in detail.get(f"items_up_star_{star}") or []:
                add(item, star, "weapon" if kind == "weapon" else "role", True)
        rate5, rate4 = rate(detail["base_prob_star5"]), rate(detail["base_prob_star4"])
        up5 = 0 if kind == "permanent" else rate(detail["up_prob"])
        up4 = 7500 if kind == "weapon" else 5000
    if not all(groups.values()) or not rate5 or not rate4 or rate5 + rate4 >= 10000:
        raise PoolError("官方卡池物品或概率不完整")
    if kind != "permanent" and (not up[5] or not up5):
        raise PoolError("官方活动卡池 UP 数据不完整")
    if kind != "permanent" and not group.startswith("collab") and not up[4]:
        raise PoolError("官方活动卡池四星 UP 数据不完整")
    for star in (4, 5):
        if up[star] and not set(groups[star]) - set(up[star]):
            raise PoolError("官方活动卡池非 UP 数据不完整")

    def names(star: int, item_type: str | None = None) -> list[str]:
        return [
            n
            for n in groups[star]
            if n not in up.get(star, []) and (item_type is None or items[n]["type"] == item_type)
        ]

    start, end = timestamp(row["begin_time"]), timestamp(row["end_time"])
    if start >= end:
        raise PoolError("官方卡池开放时间异常")
    return {
        "id": str(row["gacha_id"]),
        "game": game,
        "kind": kind,
        "group": group,
        "title": re.sub(r"<[^>]*>", "", detail["title"]),
        "start": start,
        "end": end,
        "up5": up[5],
        "up4": up[4],
        "five": names(5, "role" if kind == "permanent" else None),
        "fiveW": names(5, "weapon") if kind == "permanent" else [],
        "role4": names(4, "role"),
        "weapon4": names(4, "weapon"),
        "weapon3": names(3),
        "items": items,
        "rate5": rate5,
        "rate4": rate4,
        "upRate5": up5,
        "upRate4": up4,
    }


def parse_zzz_icon_meta(avatars: dict, weapons: dict) -> dict[int, str]:
    """把 Enka 的绝区零物品 ID → 可下载 UI 图片 URL，忽略异常路径。"""
    icons: dict[int, str] = {}
    for source, field in ((avatars, "Image"), (weapons, "ImagePath")):
        if not isinstance(source, dict):
            raise PoolError("绝区零图片元数据格式错误")
        for raw_id, data in source.items():
            if not isinstance(data, dict):
                continue
            path = str(data.get(field) or "")
            if not re.fullmatch(r"/ui/zzz/[A-Za-z0-9_.-]+\.(?:png|webp)", path):
                continue
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            icons[item_id] = f"{ENKA_UI_BASE}{path}"
    if not icons:
        raise PoolError("绝区零图片元数据为空")
    return icons


def parse_sr_icon_meta(avatars: dict, weapons: dict, localization: dict) -> tuple[dict[int, str], dict[str, str]]:
    """把 Enka 的星铁物品 ID/中文名 → 可下载 UI 图片 URL。"""
    icons: dict[int, str] = {}
    names: dict[str, str] = {}
    zh_cn = localization.get("zh-cn") if isinstance(localization, dict) else None
    if not isinstance(zh_cn, dict):
        raise PoolError("星铁图片本地化元数据格式错误")
    for source, field, name_field in (
        (avatars, "AvatarCutinFrontImgPath", "AvatarName"),
        (weapons, "ImagePath", "EquipmentName"),
    ):
        if not isinstance(source, dict):
            raise PoolError("星铁图片元数据格式错误")
        for raw_id, data in source.items():
            if not isinstance(data, dict):
                continue
            path = str(data.get(field) or "")
            if ".." in path or not re.fullmatch(r"/ui/hsr/[A-Za-z0-9_./-]+\.(?:png|webp)", path):
                continue
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            image = f"{ENKA_UI_BASE}{path}"
            icons[item_id] = image
            raw_name = data.get(name_field)
            name_hash = raw_name.get("Hash") if isinstance(raw_name, dict) else None
            name = str(zh_cn.get(str(name_hash)) or "").strip()
            if name:
                names[name] = image
    if not icons:
        raise PoolError("星铁图片元数据为空")
    return icons, names


async def fetch_icon_meta(game: str, client: httpx.AsyncClient) -> tuple[dict[int, str], dict[str, str]]:
    urls = ICON_META_URLS.get(game)
    if not urls:
        return {}, {}
    responses = await asyncio.gather(*(client.get(url) for url in urls))
    for response in responses:
        response.raise_for_status()
    payloads = [response.json() for response in responses]
    if game == "sr":
        return parse_sr_icon_meta(*payloads)
    return parse_zzz_icon_meta(*payloads), {}


def apply_item_icons(game: str, pools: list[dict], icons: dict[int, str], names: dict[str, str] | None = None) -> int:
    """只补空图片，不覆盖卡池官方直接提供的 UP 图片。返回补全数。"""
    filled = 0
    for pool in pools:
        if pool.get("game") != game:
            continue
        for item in pool.get("items", {}).values():
            if item.get("imgFile"):
                continue
            image = icons.get(int(item.get("itemId") or 0)) or (names or {}).get(str(item.get("name") or ""))
            if image:
                item["imgFile"] = image
                filled += 1
    return filled


async def fetch_pools(game: str, client: httpx.AsyncClient) -> list[dict]:
    check_game(game)
    base = BASE_URLS[game]
    response = await client.get(f"{base}/gacha/list.json")
    response.raise_for_status()
    payload = response.json()
    if payload.get("retcode") != 0:
        raise PoolError("官方卡池清单暂不可用")
    rows = [r for r in payload["data"]["list"] if int(r["gacha_type"]) in POOL_TYPES[game]]
    if not rows:
        raise PoolError("官方卡池清单为空")
    semaphore = asyncio.Semaphore(4)

    async def fetch(row: dict) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", str(row["gacha_id"])):
            raise PoolError("官方卡池编号异常")
        async with semaphore:
            result = await client.get(f"{base}/{row['gacha_id']}/zh-cn.json")
            result.raise_for_status()
            return parse_pool(game, row, result.json())

    # 任一受支持的池详情损坏则整次不落盘，避免悄悄丢失第二池。
    results = await asyncio.gather(*(fetch(row) for row in rows), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    pools = list(results)
    order = {"role": 0, "weapon": 1, "permanent": 2}
    pools.sort(key=lambda p: (order[p["kind"]], p["group"].startswith("collab")))
    return pools


def _refresh_seconds() -> int:
    from nonebot import get_plugin_config

    from ..config import Config

    try:
        return get_plugin_config(Config).miao_gacha_pool_interval * 60
    except (RuntimeError, ValueError):
        return 3600


def _due(snapshot: dict, now: float) -> bool:
    fetched = snapshot.get("fetchedAt", 0)
    return (
        not snapshot
        or snapshot.get("schema") != SNAPSHOT_SCHEMA
        or now - fetched >= _refresh_seconds()
        or any(fetched < boundary <= now for p in snapshot.get("pools", []) for boundary in (p["start"], p["end"] + 1))
    )


async def ensure_pools(game: str, *, force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    """按需刷新；失败时仅允许继续使用仍在开放期内的旧缓存。"""
    check_game(game)
    async with _LOCKS[game]:
        snapshot = await asyncio.to_thread(read_snapshot, game)
        now = time.time()
        if not force and not _due(snapshot, now):
            return snapshot
        if _RETRY_AFTER.get(game, 0) > now:
            if not force and active_pools(snapshot, now):
                return {**snapshot, "warning": "卡池同步暂时失败，使用仍在有效期内的缓存"}
            raise PoolError("卡池同步暂时失败，请一分钟后重试")
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as own_client:
                    pools = await fetch_pools(game, own_client)
                    icons, icon_names = await _icons_with_fallback(game, snapshot, own_client)
            else:
                pools = await fetch_pools(game, client)
                icons, icon_names = await _icons_with_fallback(game, snapshot, client)
            if icons or icon_names:
                apply_item_icons(game, pools, icons, icon_names)
            snapshot = {"schema": SNAPSHOT_SCHEMA, "fetchedAt": time.time(), "pools": pools}
            if icons:
                snapshot["itemImages"] = {str(item_id): url for item_id, url in icons.items()}
            if icon_names:
                snapshot["itemImageNames"] = icon_names
            await asyncio.to_thread(store.save_json, cache_path(game), snapshot)
            _RETRY_AFTER.pop(game, None)
            return snapshot
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as error:
            _RETRY_AFTER[game] = time.time() + 60
            logger.warning(f"[miao] {GAME_NAMES[game]}卡池同步失败: {error}")
            if not force and active_pools(snapshot, now):
                return {**snapshot, "warning": "卡池同步暂时失败，使用仍在有效期内的缓存"}
            raise PoolError(f"{GAME_NAMES[game]}卡池同步失败，请稍后发送 /更新{GAME_NAMES[game]}卡池 重试") from error


async def _icons_with_fallback(
    game: str, snapshot: dict, client: httpx.AsyncClient
) -> tuple[dict[int, str], dict[str, str]]:
    if game not in ICON_META_URLS:
        return {}, {}
    prefix = f"{ENKA_UI_BASE}/ui/{'hsr' if game == 'sr' else 'zzz'}/"
    cached = {
        int(item_id): str(url)
        for item_id, url in (snapshot.get("itemImages") or {}).items()
        if str(item_id).isdigit() and str(url).startswith(prefix)
    }
    cached_names = {
        str(name): str(url)
        for name, url in (snapshot.get("itemImageNames") or {}).items()
        if str(name).strip() and str(url).startswith(prefix)
    }
    try:
        return await fetch_icon_meta(game, client)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        logger.warning(f"[miao] {GAME_NAMES[game]}图片元数据同步失败，使用已有图片信息: {error}")
        return cached, cached_names


async def refresh_all_pools() -> None:
    """定时按缓存期限检查，每款游戏独立失败。"""
    results = await asyncio.gather(*(ensure_pools(g) for g in GAME_NAMES), return_exceptions=True)
    for game, result in zip(GAME_NAMES, results):
        if isinstance(result, Exception):
            logger.warning(f"[miao] {GAME_NAMES[game]}卡池自动更新失败: {result}")


def select_pool(pools: list[dict], kind: str) -> dict:
    match = re.fullmatch(r"(role|weapon|permanent)([1-9]\d*)?", kind)
    if not match:
        raise PoolError("不支持的模拟卡池类型")
    category, index = match.group(1), int(match.group(2) or 1)
    candidates = [p for p in pools if p["kind"] == category]
    if index > len(candidates):
        raise PoolError("该编号没有正在开放的卡池，请发送 /卡池列表（或 /星铁卡池列表、/绝区零卡池列表）查看")
    return candidates[index - 1]
