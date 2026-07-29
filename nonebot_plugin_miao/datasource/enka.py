"""enka.network 原神面板数据源：抓取 + 解析

移植自 refs/miao-plugin models/serv/api/EnkaApi.js（抓取）与 EnkaData.js（解析）。
解析目标是 miao 统一 avatar 结构（对齐 models/Avatar.js 消费/落盘的字段）：

    avatar = {
        id, name, elem,            # 角色 meta id/名字/元素（enka avatarId 与 meta id 一致）
        level,                     # propMap['4001'].val
        promote,                   # propMap['1002'].val（突破等级）
        cons,                      # len(talentIdList)（命座）
        fetter,                    # fetterInfo.expLevel（好感）
        costume,                   # costumeId，需在 meta costume 列表内，否则 0
        talent: {a, e, q},         # skillLevelMap 经 meta talentId 映射
        weapon: {id, name, level, promote, affix},   # equipList 中 ITEM_WEAPON 项
        artis: {                   # equipList 中圣遗物项，key 为部位序号 1-5（花羽沙杯冠）
            "1": {id, level, star, mainId, attrIds}, ...
        },
        _source, _time,            # 数据源标识与更新时间戳（秒）
    }
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..core import meta
from .errors import ProfileError

ENKA_API = "https://enka.network/api/uid/{uid}"
# UA 与 miao-plugin config/system/profile_system.js enkaApi.userAgent 一致
USER_AGENT = "Miao-Plugin/3.1"
TIMEOUT = 20

# 圣遗物 equipType → 部位序号（花1/羽2/沙3/杯4/冠5），移植自 EnkaData.js artisIdxMap
ARTIS_IDX_MAP = {
    "EQUIP_BRACER": 1,
    "EQUIP_NECKLACE": 2,
    "EQUIP_SHOES": 3,
    "EQUIP_RING": 4,
    "EQUIP_DRESS": 5,
}

# 天赋回退映射：skillLevelMap 中未在 meta talentId 命中的条目按顺序视为 a/e/q
_TALENT_FALLBACK_KEYS = ("a", "e", "q")


async def fetch_enka(uid: str | int) -> dict[str, Any]:
    """GET enka.network/api/uid/{uid}，返回原始 JSON；失败抛 ProfileError"""
    url = ENKA_API.format(uid=uid)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        raise ProfileError(f"请求 enka 失败：{e}，请稍后重试", "enka") from e
    if resp.status_code == 404:
        raise ProfileError("enka 返回 404，请确认 UID 并在游戏内打开展示柜", "enka")
    if resp.status_code == 429:
        raise ProfileError("enka 请求过于频繁（429），请稍后重试", "enka")
    if resp.status_code != 200:
        raise ProfileError(f"enka 返回 {resp.status_code}，服务可能维护中，请稍后重试", "enka")
    try:
        data = resp.json()
    except ValueError as e:
        raise ProfileError("enka 返回了无法解析的数据，服务可能维护中，请稍后重试", "enka") from e
    # 对齐 EnkaApi.js response：无 playerInfo 视为服务异常
    if not isinstance(data, dict) or not data.get("playerInfo"):
        raise ProfileError("enka 未返回玩家数据，服务可能维护中，请稍后重试", "enka")
    return data


def parse_enka(raw: dict[str, Any], uid: str | int) -> dict[str, Any]:
    """把 enka 原始响应解析成 miao Player 结构（同步纯函数）

    展示柜为空（无 avatarInfoList 或无 propMap）时抛 ProfileError，
    对齐 EnkaApi.js response 的 empty 分支。
    """
    info = raw.get("playerInfo") or {}
    avatar_list = raw.get("avatarInfoList") or []
    if not avatar_list or not avatar_list[0].get("propMap"):
        raise ProfileError(
            "展示柜为空，请将角色放在【游戏内】角色展柜，并打开【显示详情】，等待5分钟重新获取",
            "enka",
        )
    avatars: dict[str, dict[str, Any]] = {}
    for ds in avatar_list:
        avatar = _parse_avatar(ds)
        if avatar:
            avatars[str(avatar["id"])] = avatar
    if not avatars:
        raise ProfileError("展示柜角色均无法识别，请稍后重试", "enka")
    face = (info.get("profilePicture") or {}).get("avatarId") or ""
    return {
        "uid": str(uid),
        "name": info.get("nickname") or "",
        "level": info.get("level") or "",
        "word": info.get("worldLevel") or "",
        "face": face,
        "card": info.get("nameCardId") or "",
        "sign": info.get("signature") or "",
        "avatars": avatars,
        "dataSource": "enka",
        "updateTime": int(time.time()),
        # enka 建议的下次可请求间隔，供 CD 使用（对齐 EnkaApi.js cdTime）
        "ttl": raw.get("ttl") or 60,
    }


def _prop_val(prop_map: dict[str, Any], key: str) -> int:
    """propMap 取值（enka 的 val 是字符串）"""
    return int((prop_map.get(key) or {}).get("val") or 0)


def _parse_avatar(ds: dict[str, Any]) -> dict[str, Any] | None:
    """解析单个角色（移植 EnkaData.js setAvatar），meta 查不到的角色跳过"""
    char = meta.get_character(ds.get("avatarId"), "gs")
    if not char:
        return None
    prop_map = ds.get("propMap") or {}
    equip_list = ds.get("equipList") or []
    elem, talent = _parse_talent(char, ds.get("skillLevelMap") or {})
    return {
        "id": char.id,
        "name": char.name,
        "elem": elem or char.get("elem") or "",
        "level": _prop_val(prop_map, "4001"),
        "promote": _prop_val(prop_map, "1002"),
        "cons": len(ds.get("talentIdList") or []),
        "fetter": (ds.get("fetterInfo") or {}).get("expLevel") or 0,
        "costume": _check_costume(char, ds.get("costumeId")),
        "talent": talent,
        "weapon": _parse_weapon(equip_list),
        "artis": _parse_artis(equip_list),
        "_source": "enka",
        "_time": int(time.time()),
    }


def _check_costume(char: meta.CharacterMeta, costume_id: Any) -> int:
    """衣装校验（移植 Character.js checkCostume）：costumeId 需在 meta costume 列表内"""
    if not costume_id:
        return 0
    costumes = char.get("costume") or []
    return int(costume_id) if int(costume_id) in costumes else 0


def _parse_talent(char: meta.CharacterMeta, skill_map: dict[str, Any]) -> tuple[str, dict[str, int]]:
    """天赋解析（移植 EnkaData.js getTalent）

    skillLevelMap 的 key 先查 meta talentId 映射；未命中的按出现顺序回退为 a/e/q。
    返回 (elem, talent)：elem 取自 meta talentElem（仅旅行者等多元素角色有）。
    """
    talent_id = char.get("talentId") or {}
    talent_elem = char.get("talentElem") or {}
    elem = ""
    idx = 0
    ret: dict[str, int] = {}
    for tid, lv in skill_map.items():
        tid = str(tid)
        if tid in talent_id:
            key = talent_id[tid]
            elem = elem or talent_elem.get(tid, "")
            ret[key] = lv
        elif idx < len(_TALENT_FALLBACK_KEYS):
            key = _TALENT_FALLBACK_KEYS[idx]
            idx += 1
            # 与 JS 的 ret[key] = ret[key] || lv 等价：保留先出现的值
            ret[key] = ret.get(key) or lv
    return elem, ret


def _parse_weapon(equip_list: list[dict[str, Any]]) -> dict[str, Any]:
    """武器解析（移植 EnkaData.js getWeapon）：equipList 中唯一的 ITEM_WEAPON 项"""
    for item in equip_list:
        flat = item.get("flat") or {}
        if flat.get("itemType") != "ITEM_WEAPON":
            continue
        weapon = item.get("weapon") or {}
        w_meta = meta.get_weapon_by_id(item.get("itemId"), "gs")
        affix_map = weapon.get("affixMap") or {}
        # affixMap 只有一个条目，值为精炼阶数-1（对齐 lodash.values(affixMap)[0] + 1）
        affix = (next(iter(affix_map.values()), 0) or 0) + 1
        return {
            "id": item.get("itemId"),
            "name": w_meta["name"] if w_meta else "",
            "level": weapon.get("level") or 1,
            "promote": weapon.get("promoteLevel") or 0,
            "affix": affix,
        }
    return {}


def _parse_artis(equip_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """圣遗物解析（移植 EnkaData.js getArtifact），key 为部位序号 1-5"""
    ret: dict[str, dict[str, Any]] = {}
    for item in equip_list:
        flat = item.get("flat") or {}
        idx = ARTIS_IDX_MAP.get(flat.get("equipType"))
        if not idx:
            continue
        reliquary = item.get("reliquary") or {}
        ret[str(idx)] = {
            "id": item.get("itemId"),
            # enka 的 reliquary.level 比游戏内等级大 1（对齐源码 level - 1，上限 20）
            "level": min(20, (reliquary.get("level") or 1) - 1),
            "star": flat.get("rankLevel") or 5,
            "mainId": reliquary.get("mainPropId"),
            "attrIds": reliquary.get("appendPropIdList") or [],
        }
    return ret
