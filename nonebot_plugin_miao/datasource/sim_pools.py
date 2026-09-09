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
# (抽取类型, 保底分组)：联动跃迁与普通跃迁独立，复刻池与普通活动池共享。
POOL_TYPES = {
    "gs": {200: ("permanent", "permanent"), 301: ("role", "role"),
           400: ("role", "role"), 302: ("weapon", "weapon")},
    "sr": {1: ("permanent", "permanent"), 11: ("role", "role"), 12: ("weapon", "weapon"),
           21: ("role", "collab_role"), 22: ("weapon", "collab_weapon")},
    "zzz": {1001: ("permanent", "permanent"),
            **{k: ("role", "role") for k in (2001, 2002, 2011, 2012)},
            **{k: ("weapon", "weapon") for k in (3001, 3002, 3011, 3012)}},
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
    return data if isinstance(data, dict) and data.get("schema") == 1 else {}


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

    def add(raw: dict, star: int, item_type: str, is_up: bool = False) -> None:
        name = str(raw.get("item_name") or "").strip()
        if not name:
            raise PoolError("官方卡池物品名称缺失")
        previous = items.get(name, {})
        items[name] = {
            "name": name, "star": star, "type": item_type,
            "element": raw.get("item_attr") or previous.get("element", ""),
            "imgFile": raw.get("item_img") or raw.get("image_url") or previous.get("imgFile", ""),
        }
        if name not in groups[star]:
            groups[star].append(name)
        if is_up and name not in up[star]:
            up[star].append(name)

    if game == "gs":
        for star in (3, 4, 5):
            for item in detail.get(f"r{star}_prob_list") or []:
                add(item, star, "role" if item["item_type"] == "角色" else "weapon",
                    bool(item.get("is_up")))
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
        return [n for n in groups[star] if n not in up.get(star, [])
                and (item_type is None or items[n]["type"] == item_type)]

    start, end = timestamp(row["begin_time"]), timestamp(row["end_time"])
    if start >= end:
        raise PoolError("官方卡池开放时间异常")
    return {
        "id": str(row["gacha_id"]), "game": game, "kind": kind, "group": group,
        "title": re.sub(r"<[^>]*>", "", detail["title"]), "start": start, "end": end,
        "up5": up[5], "up4": up[4], "five": names(5, "role" if kind == "permanent" else None),
        "fiveW": names(5, "weapon") if kind == "permanent" else [],
        "role4": names(4, "role"), "weapon4": names(4, "weapon"), "weapon3": names(3),
        "items": items, "rate5": rate5, "rate4": rate4, "upRate5": up5, "upRate4": up4,
    }


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
    return (not snapshot or now - fetched >= _refresh_seconds()
            or any(fetched < boundary <= now for p in snapshot.get("pools", [])
                   for boundary in (p["start"], p["end"] + 1)))


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
            else:
                pools = await fetch_pools(game, client)
            snapshot = {"schema": 1, "fetchedAt": time.time(), "pools": pools}
            await asyncio.to_thread(store.save_json, cache_path(game), snapshot)
            _RETRY_AFTER.pop(game, None)
            return snapshot
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as error:
            _RETRY_AFTER[game] = time.time() + 60
            logger.warning(f"[miao] {GAME_NAMES[game]}卡池同步失败: {error}")
            if not force and active_pools(snapshot, now):
                return {**snapshot, "warning": "卡池同步暂时失败，使用仍在有效期内的缓存"}
            raise PoolError(f"{GAME_NAMES[game]}卡池同步失败，请稍后发送 /更新{GAME_NAMES[game]}卡池 重试") from error


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
