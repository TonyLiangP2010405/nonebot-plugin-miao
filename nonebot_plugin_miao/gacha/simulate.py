"""三游戏模拟抽卡：官方卡池物品/基础概率，独立存档与可注入随机源。

普通角色活动池共享保底，武器/光锥/音擎、常驻及星铁联动池各自独立。
软保底采用模拟递增曲线，不声称复刻游戏未公开的完整随机算法。
"""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from ..core import meta, store
from ..datasource import sim_pools

Rng = Callable[[int, int], int]
_SYS_RAND = random.SystemRandom()
_STATE_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _default_rng(min_: int, max_: int) -> int:
    return _SYS_RAND.randint(min_, max_)


def _sample(seq: list, rng: Rng) -> Any:
    if not seq:
        raise sim_pools.PoolError("卡池物品不完整，请更新卡池后重试")
    return seq[rng(1, len(seq)) - 1]


def get_end() -> dict[str, int]:
    """按国服 UTC+8 每日 04:00 重置，与机器时区无关。"""
    dt = datetime.fromtimestamp(_now(), sim_pools.CN_TZ)
    reset = dt.replace(hour=4, minute=0, second=0, microsecond=0)
    if reset <= dt:
        reset += timedelta(days=1)
    return {"end": int(dt.replace(hour=23, minute=59, second=59).timestamp()), "end4": int(reset.timestamp())}


def get_week_end() -> int:
    dt = datetime.fromtimestamp(_now(), sim_pools.CN_TZ)
    end = (dt + timedelta(days=6 - dt.weekday())).replace(hour=23, minute=59, second=59)
    return int(end.timestamp())


def _gacha_type(kind: str) -> str:
    match = re.fullmatch(r"(role|weapon|permanent)([1-9]\d*)?", kind)
    if not match:
        raise sim_pools.PoolError("不支持的模拟卡池类型")
    return match.group(1)


def get_pool(kind: str, now: float | None = None, game: str = "gs") -> dict:
    snapshot = sim_pools.read_snapshot(game)
    return sim_pools.select_pool(sim_pools.active_pools(snapshot, _now() if now is None else now), kind)


def _state_key(scope_key: str, game: str) -> str:
    sim_pools.check_game(game)
    # 原神继续沿用原路径，保留已有保底；新增游戏加前缀。
    return scope_key if game == "gs" else f"{game}:{scope_key}"


def _counter() -> dict:
    return {"num4": 0, "isUp4": 0, "num5": 0, "isUp5": 0}


def _new_user() -> dict:
    return {
        "role": _counter(), "permanent": _counter(),
        "weapon": {**_counter(), "lifeNum": 0, "type": 0},
        "today": {"star": [], "expire": get_end()["end4"], "num": 0, "weaponNum": 0},
        "week": {"num": 0, "expire": get_week_end()},
    }


def load_user(scope_key: str, game: str = "gs") -> dict:
    user = store.read_sim_state(_state_key(scope_key, game)) or _new_user()
    if _now() >= user["today"]["expire"]:
        user["today"] = _new_user()["today"]
    if _now() >= user["week"]["expire"]:
        user["week"] = _new_user()["week"]
    return user


def save_user(scope_key: str, user: dict, game: str = "gs") -> None:
    store.write_sim_state(_state_key(scope_key, game), user)


def _default_daily_limit() -> int:
    from nonebot import get_plugin_config

    from ..config import Config

    try:
        return get_plugin_config(Config).miao_gacha_daily_limit
    except (RuntimeError, ValueError):
        return 1


def _limit_message(user: dict, count: int, daily_limit: int | None) -> str | None:
    limit = _default_daily_limit() if daily_limit is None else daily_limit
    used = user["today"]["num"] + user["today"]["weaponNum"]
    remaining = max(0, limit * 10 - used)
    if remaining >= count:
        return None
    return f"今日已抽 {used} 抽，剩余 {remaining} 抽额度；本次需要 {count} 抽"


def check_limit(scope_key: str, gacha_type: str, is_master: bool = False,
                daily_limit: int | None = None, *, game: str = "gs", count: int = 10) -> str | None:
    return None if is_master else _limit_message(load_user(scope_key, game), count, daily_limit)


def _sync_fate(user: dict, pool: dict) -> None:
    state = user["weapon"]
    # 旧存档没有期次标记，首次使用清掉无法归属期次的命定值和定轨。
    if state.get("bannerId") != pool["id"]:
        state.update(bannerId=pool["id"], lifeNum=0, type=0)


def get_bing_weapon(pool: dict, user: dict, gacha_type: str, short_name: bool = False) -> str | None:
    if pool["game"] != "gs" or gacha_type != "weapon":
        return None
    index = user["weapon"].get("type", 0)
    return pool["up5"][index - 1] if 1 <= index <= len(pool["up5"]) else None


def toggle_bing(scope_key: str, target: int | None = None, *, pool: dict | None = None) -> str:
    """/定轨 循环选择，/定轨1、/定轨2 或 /定轨0 明确选择。"""
    pool = pool or get_pool("weapon")
    with _STATE_LOCK:
        user = load_user(scope_key)
        _sync_fate(user, pool)
        state = user["weapon"]
        if target is None:
            target = (state["type"] + 1) % (len(pool["up5"]) + 1)
        if not 0 <= target <= len(pool["up5"]):
            raise sim_pools.PoolError(f"定轨编号必须是 0-{len(pool['up5'])}，0 表示取消")
        if target != state["type"]:
            state.update(type=target, lifeNum=0)
        save_user(scope_key, user)
        if not target:
            return "定轨已取消"
        return "定轨成功\n" + "\n".join(
            f"{'[√]' if i == target else '[  ]'} {name}" for i, name in enumerate(pool["up5"], 1)
        )


def probability(user: dict, gacha_type: str, pool: dict) -> int:
    pull = user[gacha_type]["num5"] + 1
    weapon = pool["kind"] == "weapon"
    hard, soft = (80, 63) if weapon else (90, 74)
    if pull >= hard:
        return 10000
    return min(10000, pool["rate5"] + max(0, pull - soft + 1) * (700 if weapon else 600))


def _ordinary(pool: dict, star: int, rng: Rng) -> str:
    if star == 5:
        choices = [v for v in (pool["five"], pool["fiveW"]) if v]
    else:
        choices = [v for v in (pool["role4"], pool["weapon4"]) if v]
    # 非 UP 的角色/装备分支等概率，再在分支内均匀选择。
    return _sample(_sample(choices, rng), rng)


def _item(pool: dict, name: str) -> dict:
    item = dict(pool["items"][name])
    if not item["imgFile"] and pool["game"] in ("gs", "sr"):
        try:
            image = meta.char_img if item["type"] == "role" else meta.weapon_img
            item["imgFile"] = image(name, "gacha", pool["game"])
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
    return item


def lottery(user: dict, pool: dict, gacha_type: str, rng: Rng, count: int = 10) -> list[dict]:
    counter = user.setdefault(gacha_type, _counter())
    res: list[dict] = []
    seen: set[str] = set()
    for index in range(1, count + 1):
        user["today"]["weaponNum" if pool["kind"] == "weapon" else "num"] += 1
        big_up = fate = False
        number = None
        chance5 = probability(user, gacha_type, pool)
        if rng(1, 10000) <= chance5:
            star, number = 5, counter["num5"] + 1
            target = get_bing_weapon(pool, user, gacha_type)
            if target and counter["lifeNum"] >= 1:
                name, fate = target, True
            elif pool["up5"] and (counter["isUp5"] or rng(1, 10000) <= pool["upRate5"]):
                name, big_up = _sample(pool["up5"], rng), bool(counter["isUp5"])
            else:
                name = _ordinary(pool, 5, rng)
            counter["isUp5"] = int(bool(pool["up5"]) and name not in pool["up5"])
            if target:
                counter["lifeNum"] = 0 if name == target else 1
            counter["num5"] = 0
            counter["num4"] += 1
            user["today"]["star"].append({"name": name, "num": number})
            user["week"]["num"] += 1
        else:
            counter["num5"] += 1
            # 五星优先；四星基础概率换算成未中五星时的条件概率。
            chance4 = min(10000, round(pool["rate4"] * 10000 / (10000 - chance5)))
            if counter["num4"] >= 9 or rng(1, 10000) <= chance4:
                star = 4
                if pool["up4"] and (counter["isUp4"] or rng(1, 10000) <= pool["upRate4"]):
                    name = _sample(pool["up4"], rng)
                else:
                    name = _ordinary(pool, 4, rng)
                counter["isUp4"] = int(bool(pool["up4"]) and name not in pool["up4"])
                counter["num4"] = 0
            else:
                star = 3
                counter["num4"] += 1
                name = _sample(pool["weapon3"], rng)
        item = _item(pool, name)
        item.update(star=star, num=number, index=index, isBigUP=big_up, isBing=fate,
                    have=name in seen if star >= 4 else False)
        res.append(item)
        seen.add(name)
    res.sort(key=lambda v: (-v["star"], v["index"]))
    return res


def lottery_info(user: dict, res: list[dict], pool: dict, gacha_type: str) -> dict:
    fives = [v for v in res if v["star"] == 5]
    info = f"累计「{user[gacha_type]['num5']}抽」"
    if fives:
        info = " / ".join(f"{v['name']}「{v['num']}抽」" + ("大保底" if v["isBigUP"] else "")
                          + ("定轨" if v["isBing"] else "") for v in fives)
    weapon_label = {"gs": "武器", "sr": "光锥", "zzz": "音擎"}[pool["game"]]
    label = {"role": "角色池", "weapon": weapon_label + "池", "permanent": "常驻池"}[pool["kind"]]
    return {"info": info, "nowFive": len(fives), "nowFour": sum(v["star"] == 4 for v in res),
            "poolName": f"{label}：{' / '.join(pool['up5']) or pool['title']}",
            "isWeapon": pool["kind"] == "weapon", "bingWeapon": get_bing_weapon(pool, user, gacha_type),
            "lifeNum": user[gacha_type].get("lifeNum", 0)}


def do_gacha(scope_key: str, kind: str, is_master: bool = False, daily_limit: int | None = None,
             rng: Rng | None = None, *, game: str = "gs", count: int = 10, pool: dict | None = None) -> dict:
    """执行并保存真正的 1/10 抽；网络刷新由命令层提前 await 完成。"""
    sim_pools.check_game(game)
    category = _gacha_type(kind)
    if count not in (1, 10):
        raise ValueError("仅支持单抽或十连")
    pool = pool or get_pool(kind, game=game)
    if pool["game"] != game or pool["kind"] != category:
        raise sim_pools.PoolError("所选卡池与游戏/抽卡类型不一致")
    if not pool["start"] <= _now() <= pool["end"]:
        raise sim_pools.PoolError("所选卡池尚未开放或已经结束，请重新查看卡池列表")
    # 读状态→检查额度→抽取→保存放在同一锁内，防止并发消息覆盖保底或绕过额度。
    with _STATE_LOCK:
        user = load_user(scope_key, game)
        message = None if is_master else _limit_message(user, count, daily_limit)
        if message is not None:
            return {"code": "limit", "msg": message}
        if game == "gs" and category == "weapon":
            _sync_fate(user, pool)
        res = lottery(user, pool, pool["group"], rng or _default_rng, count)
        save_user(scope_key, user, game)
        return {"code": "ok", "game": game, "count": count, "list": res,
                **lottery_info(user, res, pool, pool["group"])}
