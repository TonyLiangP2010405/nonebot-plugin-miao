"""mihomo 星穹铁道面板数据源：抓取 + 解析

移植自 refs/miao-plugin models/serv/api/HomoApi.js（内含 HomoData 解析逻辑，
EnkaHSRApi.js 结构一致，可对照）。解析目标是 miao 统一 avatar 结构：

    avatar = {
        id, name, elem,            # 角色 meta id/名字/属性（mihomo avatarId 与 meta id 一致）
        level,
        promote,                   # promotion（晋阶）
        cons,                      # rank（星魂），无 rank 字段时为 0
        talent: {a, e, q, t, ...}, # skillTreeList 中天赋项（meta talentId 或 id 后缀映射）
        trees: [...],              # skillTreeList 中行迹项（level 为 1 的加成节点），
                                   # 经 meta tree 归一化为完整节点 id，后续属性计算用
        weapon: {id, name, level, promote, affix},   # equipment：tid/level/promotion/rank
        artis: {                   # relicList，key 为部位序号 1-6
            "1": {id, level, mainId, attrIds: ["affixId,cnt,step", ...]}, ...
        },
        _source, _time,            # 数据源标识与更新时间戳（秒）
    }
"""
from __future__ import annotations

import copy
import re
import time
from typing import Any

import httpx

from ..core import meta
from .errors import ProfileError

# URL 与 miao-plugin config/system/profile_system.js homoApi 一致（该配置未设 UA）
MIHOMO_API = "https://api.mihomo.me/sr_info/{uid}"
TIMEOUT = 20

# 含加强数据的角色 id（移植 Character.js enhancedCharIds）：
# mihomo 返回 avatarId=原 id + enhancedId=1 时，面板归入 2 开头的加强角色 id
ENHANCED_CHAR_IDS = {1212, 1205, 1005, 1006, 1004, 1102, 1217, 1310, 1306, 1307}

# 星铁天赋 id 后缀映射（移植 Character.js getTalentKey）：
# pointId 去掉前缀「可选的1 + 角色 id」后按后缀判定天赋类型
_TALENT_SUFFIX_MAP = {
    "001": "a",
    "002": "e",
    "003": "q",
    "004": "t",
    "007": "z",
    "301": "me",
    "302": "mt",
    "420": "xe",
}

# 行迹归一化正则（移植 Avatar.js setTrees）
_TREE_KEY_RE = re.compile(r"1?(\d{4})(\d{3})")
_TREE_ID_RE = re.compile(r"1?\d{4}(\d{3})")


async def fetch_mihomo(uid: str | int) -> dict[str, Any]:
    """GET api.mihomo.me/sr_info/{uid}，返回原始 JSON；失败抛 ProfileError"""
    url = MIHOMO_API.format(uid=uid)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        raise ProfileError(f"请求 mihomo 失败：{e}，请稍后重试", "mihomo") from e
    if resp.status_code == 404:
        raise ProfileError("mihomo 未找到该 UID（404），请确认 UID 是否正确", "mihomo")
    if resp.status_code == 429:
        raise ProfileError("mihomo 请求过于频繁（429），请稍后重试", "mihomo")
    if resp.status_code != 200:
        raise ProfileError(f"mihomo 返回 {resp.status_code}，服务可能维护中，请稍后重试", "mihomo")
    try:
        data = resp.json()
    except ValueError as e:
        raise ProfileError("mihomo 返回了无法解析的数据，服务可能维护中，请稍后重试", "mihomo") from e
    # 对齐 HomoApi.js response：无 detailInfo 视为服务异常
    if not isinstance(data, dict) or not data.get("detailInfo"):
        raise ProfileError("mihomo 未返回玩家数据，服务可能维护中，请稍后重试", "mihomo")
    return data


def parse_mihomo(raw: dict[str, Any], uid: str | int) -> dict[str, Any]:
    """把 mihomo 原始响应解析成 miao Player 结构（同步纯函数）

    展示柜为空（assistAvatarList 与 avatarDetailList 均无角色）时抛 ProfileError，
    对齐 HomoApi.js response 的 empty 分支。
    """
    info = raw.get("detailInfo") or {}
    # 支援角色 + 展示柜角色合并，展示柜角色覆盖同 id 支援角色（对齐 HomoApi.js）
    avatars_raw: dict[Any, dict[str, Any]] = {}
    for ds in info.get("assistAvatarList") or []:
        avatars_raw[ds.get("avatarId")] = ds
    for ds in info.get("avatarDetailList") or []:
        avatars_raw[ds.get("avatarId")] = ds
    if not avatars_raw:
        raise ProfileError(
            "展示柜为空，请将角色放在【游戏内】角色展柜，并打开【显示详情】，等待5分钟重新获取",
            "mihomo",
        )
    avatars: dict[str, dict[str, Any]] = {}
    for ds in avatars_raw.values():
        avatar = _parse_avatar(ds)
        if avatar:
            avatars[str(avatar["id"])] = avatar
    if not avatars:
        raise ProfileError("展示柜角色均无法识别，请稍后重试", "mihomo")
    return {
        "uid": str(uid),
        "name": info.get("nickname") or "",
        "level": info.get("level") or "",
        "word": info.get("worldLevel") or info.get("level") or "",
        "face": info.get("headIcon") or "",
        "card": info.get("personalCardId") or "",
        "sign": info.get("signature") or "",
        "avatars": avatars,
        "dataSource": "mihomo",
        "updateTime": int(time.time()),
        "ttl": raw.get("ttl") or 60,
    }


def _parse_avatar(ds: dict[str, Any]) -> dict[str, Any] | None:
    """解析单个角色（移植 HomoData.setAvatar），meta 查不到的角色跳过"""
    # 加强角色 id 重映射（移植 HomoApi.js updatePlayer 的 enhancedId 处理）：
    # 1212 → 2212，同时 skillTreeList 中 "1{原id}..." 前缀的 pointId 换成新 id 前缀
    if ds.get("enhancedId") == 1 and ds.get("avatarId") in ENHANCED_CHAR_IDS:
        ds = copy.deepcopy(ds)
        old_id = str(ds["avatarId"])
        new_id = int("2" + old_id[1:])
        ds["avatarId"] = new_id
        for skill in ds.get("skillTreeList") or []:
            point_id = str(skill.get("pointId"))
            if point_id.startswith("1" + old_id):
                skill["pointId"] = int(point_id.replace("1" + old_id, str(new_id), 1))
    char = meta.get_character(ds.get("avatarId"), "sr")
    if not char:
        return None
    talent, trees = _parse_talent(ds.get("skillTreeList") or [], char)
    return {
        "id": char.id,
        "name": char.name,
        "elem": char.get("elem") or "",
        "level": ds.get("level") or 1,
        "promote": ds.get("promotion") or 0,
        "cons": ds.get("rank") or 0,
        "talent": talent,
        "trees": trees,
        "weapon": _parse_weapon(ds.get("equipment")),
        "artis": _parse_artis(ds.get("relicList") or []),
        "_source": "mihomo",
        "_time": int(time.time()),
    }


def _talent_key(char: meta.CharacterMeta, point_id: Any) -> str | None:
    """pointId → 天赋 key（移植 Character.js getTalentKey）"""
    talent_id = char.get("talentId") or {}
    pid = str(point_id)
    if pid in talent_id:
        return talent_id[pid]
    # 正常天赋 id 前是否存在 1 可判断是否为加强状态，暂时忽略 1 的存在
    suffix = re.sub(rf"^1?{char.id}", "", pid)
    return _TALENT_SUFFIX_MAP.get(suffix)


def _parse_talent(skill_tree: list[dict[str, Any]], char: meta.CharacterMeta) -> tuple[dict[str, int], list[str]]:
    """天赋与行迹拆分（移植 HomoData.getTalent）

    skillTreeList 每项：能映射到天赋 key（或 level > 1）的进 talent，
    否则视为行迹加成节点进 trees（归一化为 meta tree 的完整节点 id）。
    """
    talent: dict[str, int] = {}
    tree_ids: list[Any] = []
    for d in skill_tree:
        key = _talent_key(char, d.get("pointId"))
        level = d.get("level") or 0
        if key or level > 1:
            talent[key or str(d.get("pointId"))] = level
        else:
            tree_ids.append(d.get("pointId"))
    return talent, _normalize_trees(tree_ids, char)


def _normalize_trees(tree_ids: list[Any], char: meta.CharacterMeta) -> list[str]:
    """行迹节点 id 归一化（移植 Avatar.js setTrees）

    星铁行迹 pointId 的 4 位角色前缀在不同命运下不同，按 meta tree 的 key
    取后 3 位做映射归一；另补 100-103 的分支节点（解锁小行迹的前置）。
    """
    tree_meta = char.get("tree") or {}
    prefix = ""
    suffix_map: dict[str, str] = {}
    for key in tree_meta:
        m = _TREE_KEY_RE.search(str(key))
        if m:
            prefix = prefix or m.group(1)
            suffix_map[m.group(2)] = str(key)
    if prefix:
        for i in range(4):
            suffix_map[f"10{i}"] = f"{prefix}10{i}"
    ret: list[str] = []
    for pid in tree_ids:
        m = _TREE_ID_RE.search(str(pid))
        suffix = m.group(1) if m else str(pid)
        ret.append(suffix_map.get(suffix, str(pid)))
    return ret


def _parse_weapon(equipment: dict[str, Any] | None) -> dict[str, Any]:
    """光锥解析（移植 HomoData.setAvatar 的 equipment 取值：id:tid, promote:promotion, affix:rank）"""
    if not equipment:
        return {}
    w_meta = meta.get_weapon_by_id(equipment.get("tid"), "sr")
    return {
        "id": equipment.get("tid"),
        "name": w_meta["name"] if w_meta else "",
        "level": equipment.get("level") or 1,
        "promote": equipment.get("promotion") or 0,
        "affix": equipment.get("rank") or 0,
    }


def _parse_artis(relic_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """遗器解析（移植 HomoData.getArtis），key 为 relic.type 部位序号 1-6

    attrIds 元素为 "affixId,cnt,step" 字符串（与 miao 落盘格式一致），
    其中 cnt 是词条次数、step 是强化次数（无 step 时补 0）。
    """
    ret: dict[str, dict[str, Any]] = {}
    for ds in relic_list:
        attr_ids: list[str] = []
        for s in ds.get("subAffixList") or []:
            if not s.get("affixId"):
                continue
            attr_ids.append(",".join([str(s["affixId"]), str(s.get("cnt") or 0), str(s.get("step") or 0)]))
        ret[str(ds.get("type"))] = {
            "id": ds.get("tid"),
            "level": ds.get("level") or 0,
            "mainId": ds.get("mainAffixId"),
            "attrIds": attr_ids,
        }
    return ret
