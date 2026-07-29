"""抽卡分析逻辑：卡池记录分析 analyse + 按版本统计 stat

逐行移植自 refs/miao-plugin/apps/gacha/GachaData.js 的 readJSON / analyse / stat / getVersion。
返回结构字段名与 JS 版保持一致（camelCase），渲染层直接消费；
空记录时返回 None（对应 JS 版返回 false）。

已知的 JS 原版行为（照抄，不修正）：
- weaponNum / bigNum 声明后从未自增，恒为 0
- stat 里池名简称用 Character.get(name)（默认 gs 优先、跨游戏兜底匹配）

与 JS 版的差异（有意修正）：
- JS 版 "新版本" 兜底区间的结束时间硬编码为 2025-12-31（上游每次手动更新，
  过期后新记录会落入 "未知"）。本版改为 max(2025-12-31, 最后卡池结束 + 21 天)，
  约覆盖一个卡池半期，数据未更新时新记录仍归入 "新版本" 而非 "未知"。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..core import meta, store

_TIME_FMT = "%Y-%m-%d %H:%M:%S"

# "新版本" 兜底区间的最短结束时间（JS 版的硬编码值，仅作下限）
_FALLBACK_END = "2025-12-31 23:59:59"
# 兜底区间在最后卡池结束后再延长的天数（约一个卡池半期）
_FALLBACK_EXTEND_DAYS = 21

# 未知物品占位 id（照抄 JS 版）：武器 403 / 角色 404
_UNKNOWN_WEAPON_ID = 403
_UNKNOWN_CHAR_ID = 404


def _parse_time(text: str) -> datetime:
    return datetime.strptime(text, _TIME_FMT)


def _tofixed(num: float, digits: int) -> str:
    """等价 JS Number.prototype.toFixed

    对浮点数的精确二进制值做十进制舍入，平局时远离 0（与 V8 一致）。
    """
    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(num).quantize(quantum, rounding=ROUND_HALF_UP))


def _id_order(item_id: Any) -> int:
    """物品 id 的数值排序键

    JS 对象里整数形态的 key 按数值升序迭代（与插入顺序无关），
    meta 里 gs 武器 / sr 物品的 id 是字符串，这里统一按数值排序来对齐。
    """
    return int(item_id)


# ---------------------------------------------------------------------------
# 卡池版本表（poolVersion / poolVersionSr / mixPoolVersion）
# ---------------------------------------------------------------------------

_POOL_VERSIONS: dict[str, list[dict[str, Any]]] = {}
_MIX_POOL_VERSIONS: list[dict[str, Any]] | None = None


def _build_versions(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    versions = []
    for ds in pools:
        versions.append({**ds, "start": _parse_time(ds["from"]), "end": _parse_time(ds["to"])})
    last = versions[-1]
    # 为未知卡池做兼容（start=最后一个卡池的结束；end 取 max(硬编码下限, 最后结束+21天)，
    # 修正 JS 版硬编码过期后新记录落 "未知" 的问题）
    fallback_end = max(
        _parse_time(_FALLBACK_END), last["end"] + timedelta(days=_FALLBACK_EXTEND_DAYS)
    )
    versions.append(
        {
            "version": "新版本",
            "half": "?",
            "from": last["to"],
            "to": fallback_end.strftime(_TIME_FMT),
            "start": last["end"],
            "end": fallback_end,
        }
    )
    return versions


def _pool_versions(game: str) -> list[dict[str, Any]]:
    if game not in _POOL_VERSIONS:
        _POOL_VERSIONS[game] = _build_versions(meta.pool_data(game))
    return _POOL_VERSIONS[game]


def _mix_pool_versions() -> list[dict[str, Any]]:
    global _MIX_POOL_VERSIONS
    if _MIX_POOL_VERSIONS is None:
        mix_pools = meta.pool_info("gs").get("mixPoolDetail") or []
        _MIX_POOL_VERSIONS = _build_versions(mix_pools)
    return _MIX_POOL_VERSIONS


def get_version(time: datetime, has_version: bool = True, is_mix: bool = False, game: str = "gs") -> dict[str, Any]:
    """按记录时间找所属卡池版本（getVersion 移植），找不到返回 "未知"/"全部" 兜底"""
    if is_mix:
        for ds in _mix_pool_versions():
            if ds["start"] < time < ds["end"]:
                return ds
    if has_version and game == "gs":
        for ds in _pool_versions("gs"):
            if ds["start"] < time < ds["end"]:
                return ds
    elif has_version and game == "sr":
        for ds in _pool_versions("sr"):
            if ds["start"] < time < ds["end"]:
                return ds
    return {
        "version": "全部" if has_version is False else "未知",
        "half": "",
        "char5": [],
        "char4": [],
        "weapon5": [],
        "weapon4": [],
        # JS 版兜底没有 start/end，时间比较恒为 false；用 None 表示并在调用处守卫
        "start": None,
        "end": None,
    }


# ---------------------------------------------------------------------------
# readJSON：读取某池记录并映射到物品 id
# ---------------------------------------------------------------------------


def _get_character(name: str, game: str) -> tuple[Any, str]:
    """Character.get 的移植：先按传入 game 查，再跨游戏兜底（对应 Meta.matchGame）

    返回 (CharacterMeta | None, 实际命中的 game)
    """
    char = meta.get_character(name, game)
    if char:
        return char, game
    other = "sr" if game == "gs" else "gs"
    char = meta.get_character(name, other)
    if char:
        return char, other
    return None, game


def read_json_items(user_id: str | int, uid: str | int, gacha_type: str | int, game: str) -> dict[str, Any]:
    """读取某池抽卡记录（readJSON 移植）

    返回 {"items": [{id, logId, time}]（按时间倒序）, "itemMap": {id: 物品信息}}
    按记录 id 去重；未知名称的物品映射为占位 id（武器 403 / 角色 404）。
    """
    log_json = store.read_gacha_log(user_id, uid, gacha_type, game)
    item_map: dict[Any, dict[str, Any]] = {}
    name_map: dict[str, Any] = {}
    items: list[dict[str, Any]] = []
    ids: set = set()
    for ds in log_json:
        name = ds.get("name")
        if name not in name_map:
            item_type = ds.get("item_type")
            if item_type in ("武器", "光锥"):
                weapon = meta.get_weapon(name, game)
                if weapon:
                    weapon_id = weapon.get("id")
                    weapon_name = weapon.get("name", name)
                    name_map[name] = weapon_id
                    item_map[weapon_id] = {
                        "type": "weapon",
                        "count": 0,
                        "star": weapon.get("star"),
                        "name": weapon_name,
                        # Weapon.abbr 是计算属性：名字不超过 4 个字时用全名
                        "abbr": weapon_name if len(weapon_name) <= 4 else (weapon.get("abbr") or weapon_name),
                        "img": meta.weapon_img(weapon_name, "icon", game),
                    }
                else:
                    name_map[name] = _UNKNOWN_WEAPON_ID
                    item_map[_UNKNOWN_WEAPON_ID] = {
                        "type": "weapon",
                        "count": 0,
                        "star": 3,
                        "name": "未知",
                        "abbr": "未知",
                        "img": "",
                    }
            elif item_type == "角色":
                char, char_game = _get_character(name, game)
                if char:
                    char_id = char.id
                    name_map[name] = char_id
                    item_map[char_id] = {
                        "type": "char",
                        "count": 0,
                        "star": char.get("star"),
                        "name": char.name,
                        "abbr": char.get("abbr"),
                        "img": meta.char_img(char.name, "face", char_game),
                    }
                else:
                    name_map[name] = _UNKNOWN_CHAR_ID
                    item_map[_UNKNOWN_CHAR_ID] = {
                        "type": "char",
                        "count": 0,
                        "star": 4,
                        "name": "未知",
                        "abbr": "未知",
                        "img": "",
                    }
        item_id = name_map.get(name)
        ds_id = ds.get("id")
        if not item_id or item_id not in item_map or (ds_id and ds_id in ids):
            continue
        ids.add(ds_id)
        items.append({"id": item_id, "logId": ds_id, "time": _parse_time(ds["time"])})
    items.sort(key=lambda x: x["time"], reverse=True)
    return {"items": items, "itemMap": item_map}


# ---------------------------------------------------------------------------
# analyse：单池分析
# ---------------------------------------------------------------------------


def analyse(user_id: str | int, uid: str | int, gacha_type: str | int, game: str) -> dict[str, Any] | None:
    """卡池分析（analyse 移植），无记录返回 None"""
    log_data = read_json_items(user_id, uid, gacha_type, game)
    five_log: list[dict[str, Any]] = []
    five_num = 0
    four_num = 0
    five_log_num = 0
    four_log_num = 0
    no_five_num = 0
    no_four_num = 0
    wai = 0  # 歪
    weapon_num = 0  # JS 版声明后从未自增，恒为 0（照抄）
    weapon_four_num = 0
    big_num = 0  # JS 版保留的大保底变量，逻辑上恒为 0（照抄）
    all_num = 0
    is_mix = gacha_type == 500

    item_map = log_data["itemMap"]
    if not log_data["items"]:
        return None
    curr_version: dict[str, Any] | None = None
    for item in log_data["items"]:
        if curr_version is None or (
            curr_version.get("start") is not None and item["time"] < curr_version["start"]
        ):
            curr_version = get_version(item["time"], True, is_mix, game)

        all_num += 1
        ds = item_map[item["id"]]
        star = ds["star"]
        item_type = ds["type"]
        ds["count"] += 1
        if star == 4:
            four_num += 1
            if no_four_num == 0:
                no_four_num = four_log_num
            four_log_num = 0
            if item_type == "weapon":
                weapon_four_num += 1
        four_log_num += 1

        if star == 5:
            five_num += 1
            if five_log:
                five_log[-1]["count"] = five_log_num
            else:
                no_five_num = five_log_num
            five_log_num = 0
            # 是否当期 UP：按记录时间所在版本的 char5/weapon5 判定，不在则记为歪
            if item_type == "char":
                is_up = ds["name"] in (curr_version.get("char5") or [])
            else:
                is_up = ds["name"] in (curr_version.get("weapon5") or [])
            if not is_up:
                wai += 1
            five_log.append({"id": item["id"], "isUp": is_up, "date": item["time"].strftime("%m-%d")})
        five_log_num += 1

    if five_log:
        five_log[-1]["count"] = five_log_num
    else:
        # 没有五星
        no_five_num = all_num

    # 四星最多（JS 对象整数 key 按数值升序迭代，sort 稳定，平票保持该顺序）
    four_items = [ds for _, ds in sorted(item_map.items(), key=lambda kv: _id_order(kv[0])) if ds["star"] == 4]
    four_items.append({"name": "无", "count": 0})
    four_items.sort(key=lambda d: d["count"], reverse=True)
    max_four = four_items[0]

    # 平均 5 星 / 平均 4 星（toFixed 字符串；无对应星级时为数字 0，照抄 JS）
    five_avg: Any = _tofixed((all_num - no_five_num) / five_num, 2) if five_num > 0 else 0
    four_avg: Any = _tofixed((all_num - no_four_num) / four_num, 2) if four_num > 0 else 0

    # 有效抽卡（每个 UP 五星的平均抽数）；若最近一个五星是歪的则剔除这段
    isvalid_num: Any = 0
    if five_num > 0 and five_num > wai:
        if five_log and not five_log[0]["isUp"]:
            isvalid_num = (all_num - no_five_num - five_log[0]["count"]) / (five_num - wai)
        else:
            isvalid_num = (all_num - no_five_num) / (five_num - wai)
        isvalid_num = _tofixed(isvalid_num, 2)

    # UP 平均消耗原石：JS 里 isvalidNum 已是 toFixed 后的字符串，乘 160 用的是舍入后的值
    up_ys = float(isvalid_num) * 160
    if up_ys >= 10000:
        up_ys_str: str = _tofixed(up_ys / 10000, 2) + "w"
    else:
        up_ys_str = _tofixed(up_ys, 0)

    # 小保底不歪概率
    no_wai_rate: Any = 0
    if five_num > 0:
        no_wai_rate = _tofixed((five_num - big_num - wai) / (five_num - big_num) * 100, 1)

    if no_five_num > 0:
        # 无五星时在 fiveLog 头部插占位（"已抽" 为当前垫抽数）
        five_log.insert(0, {"id": 888, "isUp": True, "count": no_five_num, "date": datetime.now().strftime("%m-%d")})
        item_map[888] = {"name": "已抽", "star": 5, "abbr": "已抽", "img": "gacha/imgs/no-avatar.webp"}

    return {
        "stat": {
            "allNum": all_num,
            "noFiveNum": no_five_num,
            "noFourNum": no_four_num,
            "fiveNum": five_num,
            "fourNum": four_num,
            "fiveAvg": five_avg,
            "fourAvg": four_avg,
            "wai": wai,
            "isvalidNum": isvalid_num,
            "weaponNum": weapon_num,
            "weaponFourNum": weapon_four_num,
            "upYs": up_ys_str,
        },
        "maxFour": max_four,
        "fiveLog": five_log,
        "noWaiRate": no_wai_rate,
        "items": item_map,
    }


# ---------------------------------------------------------------------------
# stat：按版本统计
# ---------------------------------------------------------------------------


def stat(user_id: str | int, uid: str | int, stat_type: str, game: str) -> dict[str, Any] | None:
    """按版本统计（stat 移植）

    stat_type: up / char / weapon / normal / mix / all；无记录返回 None
    """
    items: list[dict[str, Any]] = []
    item_map: dict[Any, dict[str, Any]] = {}
    has_version = True
    is_mix = False
    is_sr = game == "sr"

    def load_data(pool_id: int) -> None:
        nonlocal items
        gacha_data = read_json_items(user_id, uid, pool_id, game)
        items = items + gacha_data["items"]
        item_map.update(gacha_data["itemMap"])

    if stat_type in ("up", "char", "all"):
        load_data(11 if is_sr else 301)
    if stat_type in ("up", "weapon", "all"):
        load_data(12 if is_sr else 302)
    if stat_type in ("all", "normal"):
        has_version = False
        load_data(1 if is_sr else 200)
    if stat_type == "mix":
        is_mix = True
        load_data(500)
    if stat_type == "all" and not is_sr:
        load_data(500)

    items.sort(key=lambda x: x["time"], reverse=True)
    if not items:
        return None

    version_data: list[dict[str, Any]] = []
    curr_version: dict[str, Any] | None = None

    def get_curr() -> dict[str, Any] | None:
        """把当前版本桶汇总成 versionData 的一项"""
        if not curr_version:
            return None
        cv = curr_version
        # JS 版对缺失的 from/to 会格式化成 "Invalid date"，照抄
        if has_version:
            from_str = _parse_time(cv["from"]).strftime("%y-%m-%d") if cv.get("from") else "Invalid date"
            to_str = _parse_time(cv["to"]).strftime("%y-%m-%d") if cv.get("to") else "Invalid date"
        else:
            from_str = to_str = ""
        temp: dict[str, Any] = {
            "version": cv["version"],
            "half": cv["half"],
            "from": from_str,
            "to": to_str,
            "upIds": {},
        }
        up_name: dict[str, bool] = {}
        pool_names: list[str] = []
        temp_items: list[dict[str, Any]] = []
        for name in cv.get("char5") or []:
            up_name[name] = True
            # JS 版是 Character.get(name)：默认 gs 优先、跨游戏兜底
            # JS Array.join 会把 undefined（如无 abbr 的 sr 角色）转成空串
            char, _ = _get_character(name, "gs")
            pool_names.append(char.get("abbr") or "")
        for name in cv.get("weapon5") or []:
            up_name[name] = True
        w5_num = w5_up_num = c5_num = c5_up_num = c4_num = w4_num = w3_num = 0
        # JS 对象整数 key 按数值升序迭代
        for item_id in sorted(cv["items"], key=_id_order):
            num = cv["items"][item_id]
            item = item_map[item_id]
            is_up = item["name"] in up_name
            if is_up:
                temp["upIds"][item_id] = item["name"]
            temp_items.append({"id": item_id, "num": num, "star": item["star"], "isUp": 1 if is_up else 0})
            star = item["star"]
            if item["type"] == "char":
                if star == 5:
                    c5_num += num
                    if is_up:
                        c5_up_num += num
                else:
                    c4_num += num
            if item["type"] == "weapon":
                if star == 5:
                    w5_num += num
                    if is_up:
                        w5_up_num += num
                elif star == 4:
                    w4_num += num
                else:
                    w3_num += num
        temp["name"] = " / ".join(pool_names)
        # lodash.sortBy(...).reverse()：升序稳定排序后整体反转
        temp_items.sort(key=lambda d: (d["star"], d["num"], d["isUp"]))
        temp_items.reverse()
        temp["items"] = temp_items
        temp["stats"] = {
            "w5Num": w5_num,
            "w5UpNum": w5_up_num,
            "c5Num": c5_num,
            "c5UpNum": c5_up_num,
            "c4Num": c4_num,
            "w4Num": w4_num,
            "w3Num": w3_num,
            "upNum": w5_up_num + c5_up_num,
            "star5Num": w5_num + c5_num,
            "star4Num": w4_num + c4_num,
            "totalNum": w5_num + w4_num + w3_num + c5_num + c4_num,
        }
        return temp

    for ds in items:
        if curr_version is None or (
            has_version and curr_version.get("start") is not None and ds["time"] < curr_version["start"]
        ):
            if curr_version is not None:
                version_data.append(get_curr())
            v = get_version(ds["time"], has_version, is_mix, game)
            if not has_version:
                v["version"] = "全部统计" if stat_type == "all" else "常驻池"
            curr_version = {**v, "items": {}}
        curr_version["items"][ds["id"]] = curr_version["items"].get(ds["id"], 0) + 1
    version_data.append(get_curr())

    total_stat: dict[str, Any] = {}
    for ds in version_data:
        for key, num in ds["stats"].items():
            total_stat[key] = total_stat.get(key, 0) + num
    total_stat["avgUpNum"] = (
        0 if total_stat["upNum"] == 0 else _tofixed(total_stat["totalNum"] / total_stat["upNum"], 1)
    )

    return {"versionData": version_data, "itemMap": item_map, "totalStat": total_stat, "isMix": is_mix}


# ---------------------------------------------------------------------------
# 关键词 → 池/统计类型 的高层封装
# ---------------------------------------------------------------------------

# 卡池关键词 → gacha_type（原神 up/角色/抽卡/抽奖→301、常驻→200、武器→302、集录→500）
_GS_POOL_TYPES = {"up": 301, "角色": 301, "抽卡": 301, "抽奖": 301, "常驻": 200, "武器": 302, "集录": 500}
# 星铁：角色→11、常驻→1、武器/光锥→12
_SR_POOL_TYPES = {"up": 11, "角色": 11, "常驻": 1, "武器": 12, "光锥": 12}

# 统计关键词 → stat 的 type
_STAT_TYPES = {
    "up": "up",
    "角色": "char",
    "武器": "weapon",
    "光锥": "weapon",
    "常驻": "normal",
    "集录": "mix",
    "全部": "all",
    "all": "all",
}


def pool_type_of(keyword: str, game: str) -> int | None:
    """把用户关键词映射为 gacha_type，无法识别返回 None"""
    table = _GS_POOL_TYPES if game == "gs" else _SR_POOL_TYPES
    return table.get(str(keyword).strip())


def analyse_pool(user_id: str | int, uid: str | int, keyword: str, game: str) -> dict[str, Any] | None:
    """按关键词做单池分析，关键词无法识别或无记录时返回 None"""
    pool_type = pool_type_of(keyword, game)
    if pool_type is None:
        return None
    return analyse(user_id, uid, pool_type, game)


def stat_pool(user_id: str | int, uid: str | int, keyword: str, game: str) -> dict[str, Any] | None:
    """按关键词做版本统计，关键词无法识别或无记录时返回 None"""
    stat_type = _STAT_TYPES.get(str(keyword).strip())
    if stat_type is None:
        return None
    return stat(user_id, uid, stat_type, game)
