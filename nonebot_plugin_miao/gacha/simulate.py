"""十连模拟抽卡（仅原神）：对照 refs/Yunzai-genshin/model/gachaData.js 逐行移植

状态字段名保持 JS 的 camelCase 以便对照：
  user = {
    "permanent": {"num4": 0, "isUp4": 0, "num5": 0, "isUp5": 0},
    "role":      {"num4": 0, "isUp4": 0, "num5": 0, "isUp5": 0},
    "weapon":    {"num4": 0, "isUp4": 0, "num5": 0, "isUp5": 0,
                  "lifeNum": 0,   # 命定值
                  "type": 1},     # 定轨 0-取消 1-武器1 2-武器2
    "today": {"star": [{"name": ..., "num": ...}], "expire": ts, "num": 0, "weaponNum": 0},
    "week":  {"num": 0, "expire": ts},
  }
存 store 的 sim_state，scope_key：群聊 f"{group_id}:{user_id}"、私聊 f"private:{user_id}"。

与 JS 版的有意偏差（均不影响抽卡语义）：
- JS 新建用户时 permanent/role 共享同一个对象引用（第一次持久化前联动），这里分开
- JS 的 getBingWeapon 对非武器池返回 false，这里返回 None
- JS 在角色/常驻池也会执行 lifeNum++（得到 NaN 落盘为 null），这里只对武器池维护 lifeNum
- 结果项不带 JS 的 rand 字段（仅渲染用，本阶段不实现渲染）
- 超限提示不带 JS 的群名片前缀（命令层可自行拼接）
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from ..core import meta, store

# 随机入口：对应 lodash.random(min, max) 闭区间，可注入便于测试
Rng = Callable[[int, int], int]

_SYS_RAND = random.SystemRandom()

# gsCfg.element 等价：角色 -> 元素中文名，武器 -> 武器类型中文名
_ELEM_NAME = {
    "pyro": "火",
    "hydro": "水",
    "anemo": "风",
    "electro": "雷",
    "dendro": "草",
    "cryo": "冰",
    "geo": "岩",
}
_WEAPON_TYPE_NAME = {
    "sword": "单手剑",
    "claymore": "双手剑",
    "polearm": "长柄武器",
    "bow": "弓",
    "catalyst": "法器",
}

GACHA_KINDS = ("role", "role2", "weapon", "permanent")


def _now() -> float:
    """当前时间戳（秒），模块级以便测试 monkeypatch"""
    return time.time()


def _default_rng(min_: int, max_: int) -> int:
    return _SYS_RAND.randint(min_, max_)


def _sample(seq: list, rng: Rng) -> Any:
    """lodash.sample 等价：用注入的 rng 取一个元素"""
    return seq[rng(1, len(seq)) - 1]


def _difference(a: list, b: list) -> list:
    """lodash.difference 等价：a 中不在 b 里的元素，保持 a 的顺序"""
    return [x for x in a if x not in b]


# ---------------------------------------------------------------------------
# 时间：每日 4 点重置 / 每周日 24 点重置（对照 JS getEnd / getWeekEnd）
# ---------------------------------------------------------------------------


def get_end() -> dict[str, int]:
    """对照 JS getEnd()：end=今天 23:59:59，end4=今天/明天凌晨 4 点

    JS 用 moment().format("k")（1-24，0 点为 24）判断，这里原样保留该行为
    """
    dt = datetime.fromtimestamp(_now())
    start = int(datetime(dt.year, dt.month, dt.day).timestamp())
    end = start + 86400 - 1
    k = dt.hour if dt.hour != 0 else 24
    end4 = 4 * 3600 + (start if k < 4 else end)
    return {"end": end, "end4": end4}


def get_week_end() -> int:
    """对照 JS getWeekEnd()：moment().day(7).endOf('day')，即下一个周日 23:59:59"""
    dt = datetime.fromtimestamp(_now())
    # Python weekday(): 周一=0..周日=6；moment en locale: 周日=0..周六=6
    d = (dt.weekday() + 1) % 7
    target = (dt + timedelta(days=7 - d)).date()
    return int(datetime(target.year, target.month, target.day, 23, 59, 59).timestamp())


# ---------------------------------------------------------------------------
# 卡池（对照 JS getPool）
# ---------------------------------------------------------------------------


def _parse_end_time(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def get_now_pool(now: float | None = None) -> dict[str, Any]:
    """pool.json 倒序找第一个 endTime >= 当前时间的池，找不到用倒序最后一条

    对照 JS：poolArr.reverse().find(...) || poolArr.pop()（pop 的是倒序数组末尾）
    """
    pools = meta.gacha_sim_config()["pool"]
    now = _now() if now is None else now
    rev = list(reversed(pools))
    for p in rev:
        if now <= _parse_end_time(p["endTime"]):
            return p
    return rev[-1]


def _gacha_type(kind: str) -> str:
    if kind not in GACHA_KINDS:
        raise ValueError(f"非法抽卡类型: {kind!r}，仅支持 {GACHA_KINDS}")
    return "role" if kind == "role2" else kind


def get_pool(kind: str, now: float | None = None) -> dict[str, Any]:
    """三池的 up4/role4/weapon4/up5/five 组合逻辑（对照 JS getPool）"""
    _gacha_type(kind)
    gacha_def = meta.gacha_sim_config()["gacha"]
    now_pool = get_now_pool(now)

    if kind == "weapon":
        pool = {
            "up4": now_pool["weapon4"],
            "role4": gacha_def["role4"],
            "weapon4": _difference(gacha_def["weapon4"], now_pool["weapon4"]),
            "up5": now_pool["weapon5"],
            "five": _difference(gacha_def["weapon5"], now_pool["weapon5"]),
        }
    elif kind in ("role", "role2"):
        pool = {
            "up4": now_pool["up4"],
            "role4": _difference(gacha_def["role4"], now_pool["up4"]),
            "weapon4": gacha_def["weapon4"],
            "up5": now_pool["up5_2"] if kind == "role2" else now_pool["up5"],
            # 注意 JS 无论是否十连2，five 都用 NowPool.up5 做差集
            "five": _difference(gacha_def["role5"], now_pool["up5"]),
        }
    else:  # permanent
        pool = {
            "up4": [],
            "role4": gacha_def["role4"],
            "weapon4": gacha_def["weapon4"],
            "up5": [],
            "five": gacha_def["role5"],
            "fiveW": gacha_def["weapon5"],
        }

    pool["weapon3"] = gacha_def["weapon3"]
    return pool


# ---------------------------------------------------------------------------
# 用户状态（对照 JS userData / saveUser）
# ---------------------------------------------------------------------------


def _new_user() -> dict[str, Any]:
    def _counter() -> dict[str, int]:
        return {"num4": 0, "isUp4": 0, "num5": 0, "isUp5": 0}

    return {
        "permanent": _counter(),
        "role": _counter(),
        "weapon": {
            **_counter(),
            "lifeNum": 0,  # 命定值
            "type": 1,  # 定轨 0-取消 1-武器1 2-武器2
        },
        "today": {"star": [], "expire": get_end()["end4"], "num": 0, "weaponNum": 0},
        "week": {"num": 0, "expire": get_week_end()},
    }


def load_user(scope_key: str) -> dict[str, Any]:
    """读取用户状态并应用每日 4 点 / 每周日重置（对照 JS userData）"""
    user = store.read_sim_state(scope_key)
    if user:
        now = _now()
        if now > user["today"]["expire"]:
            user["today"] = {"star": [], "expire": get_end()["end4"], "num": 0, "weaponNum": 0}
        if now > user["week"]["expire"]:
            user["week"] = {"num": 0, "expire": get_week_end()}
    else:
        user = _new_user()
    return user


def save_user(scope_key: str, user: dict[str, Any]) -> None:
    """对照 JS saveUser：落盘前刷新 today.expire"""
    user["today"]["expire"] = get_end()["end4"]
    store.write_sim_state(scope_key, user)


# ---------------------------------------------------------------------------
# 抽奖（对照 JS lottery / lottery5 / lottery4 / lottery3 / probability）
# ---------------------------------------------------------------------------


def probability(user: dict[str, Any], gacha_type: str) -> int:
    """五星概率（万分比，对照 JS probability()）"""
    gacha_def = meta.gacha_sim_config()["gacha"]
    num5 = user[gacha_type]["num5"]

    if gacha_type in ("role", "permanent"):
        tmp = gacha_def["chance5"]
        # 增加双黄概率
        if user["week"]["num"] == 1:
            tmp *= 2
        # 保底
        if num5 >= 90:
            tmp = 10000
        elif num5 >= 74:
            # 74 抽之后逐渐增加概率
            tmp = 590 + (num5 - 74) * 530
        elif num5 >= 60:
            # 60 抽之后逐渐增加概率（注意 JS 这里用 def.chance5 而不是含双黄加成的 tmp）
            tmp = gacha_def["chance5"] + (num5 - 50) * 40
        return tmp

    # weapon
    tmp = gacha_def["chanceW5"]
    # 增加双黄概率
    if user["week"]["num"] == 1:
        tmp *= 3
    if num5 >= 80:
        tmp = 10000
    elif num5 >= 62:
        # 62 抽后逐渐增加概率
        tmp += (num5 - 61) * 700
    elif num5 >= 45:
        # 50 抽后逐渐增加概率
        tmp += (num5 - 45) * 60
    elif 10 <= num5 <= 20:
        tmp += (num5 - 10) * 30
    return tmp


def get_bing_weapon(pool: dict[str, Any], user: dict[str, Any], gacha_type: str, short_name: bool = False):
    """获取定轨的武器（对照 JS getBingWeapon），非武器池/未定轨返回 None"""
    if gacha_type != "weapon":
        return None
    bing_type = user["weapon"]["type"]
    if bing_type not in (1, 2):
        return None
    name = pool["up5"][bing_type - 1]
    if short_name:
        weapon = meta.get_weapon(name, "gs")
        return (weapon or {}).get("abbr") or name
    return name


def _element(name: str, item_type: str) -> str:
    """gsCfg.element 等价：角色给元素中文名，武器给武器类型中文名"""
    try:
        if item_type == "role":
            char = meta.get_character(name, "gs")
            return _ELEM_NAME.get(char.elem, "") if char else ""
        weapon = meta.get_weapon(name, "gs")
        return _WEAPON_TYPE_NAME.get(weapon.get("type"), "") if weapon else ""
    except Exception:
        return ""


def _img_file(name: str, item_type: str) -> str:
    """GachaData.getImg 等价：gacha 立绘相对 resources 的路径"""
    try:
        if item_type == "role":
            return meta.char_img(name, "gacha", "gs")
        return meta.weapon_img(name, "gacha", "gs")
    except Exception:
        return ""


class _Lottery:
    """一次十连的会话（对照 JS GachaData 实例上的 lottery 系列方法）"""

    def __init__(self, user: dict[str, Any], pool: dict[str, Any], gacha_type: str, rng: Rng):
        self.user = user
        self.pool = pool
        self.type = gacha_type
        self.rng = rng
        self.res: list[dict[str, Any]] = []
        self.five_have: list[str] = []
        self.four_have: list[str] = []
        self.index = 0

    def run(self) -> list[dict[str, Any]]:
        """十连抽（对照 JS lottery()），结果按 星级降序/type/have/index 排序"""
        for i in range(1, 11):
            self.index = i
            if self.type == "weapon":
                self.user["today"]["weaponNum"] += 1
            else:
                self.user["today"]["num"] += 1

            if self.lottery5():
                continue
            if self.lottery4():
                continue
            self.lottery3()

        # lodash.orderBy(res, ["star","type","have","index"], ["desc","asc","asc","asc"])
        self.res.sort(key=lambda v: (-v["star"], v["type"], v["have"], v["index"]))
        return self.res

    def lottery5(self) -> bool:
        is_big_up = False
        is_bing = False
        tmp_chance5 = probability(self.user, self.type)
        item_type = self.type
        counter = self.user[self.type]

        # 没有抽中五星
        if self.rng(1, 10000) > tmp_chance5:
            counter["num5"] += 1
            return False

        now_card_num = counter["num5"] + 1
        # 五星保底清零，四星保底数+1
        counter["num5"] = 0
        counter["num4"] += 1

        tmp_up = meta.gacha_sim_config()["gacha"]["wai"]
        # 已经小保底
        if counter["isUp5"] == 1:
            tmp_up = 101
        if self.type == "permanent":
            tmp_up = 0

        bing_weapon = get_bing_weapon(self.pool, self.user, self.type)
        if self.type == "weapon" and counter["lifeNum"] >= 2:
            # 定轨
            tmp_name = bing_weapon
            counter["lifeNum"] = 0
            is_bing = True
        elif self.rng(1, 100) <= tmp_up:
            # 当祈愿获取到5星角色时，有50%的概率为本期UP角色
            if counter["isUp5"] == 1:
                is_big_up = True
            # 大保底清零
            counter["isUp5"] = 0
            tmp_name = _sample(self.pool["up5"], self.rng)
            # 定轨清零
            if tmp_name == bing_weapon:
                counter["lifeNum"] = 0
        else:
            if self.type == "permanent":
                if self.rng(1, 100) <= 50:
                    tmp_name = _sample(self.pool["five"], self.rng)
                    item_type = "role"
                else:
                    tmp_name = _sample(self.pool["fiveW"], self.rng)
                    item_type = "weapon"
            else:
                # 歪了 大保底+1
                counter["isUp5"] = 1
                tmp_name = _sample(self.pool["five"], self.rng)

        # 命定值++（仅武器池；JS 对角色/常驻池也会执行但得到 NaN，无实际意义）
        if self.type == "weapon" and tmp_name != bing_weapon:
            counter["lifeNum"] += 1

        # 记录今天五星 / 本周五星数
        self.user["today"]["star"].append({"name": tmp_name, "num": now_card_num})
        self.user["week"]["num"] += 1

        # 重复抽中转换星辉
        have = tmp_name in self.five_have
        if not have:
            self.five_have.append(tmp_name)

        self.res.append(
            {
                "name": tmp_name,
                "star": 5,
                "type": item_type,
                "num": now_card_num,
                "element": _element(tmp_name, item_type),
                "index": self.index,
                "isBigUP": is_big_up,
                "isBing": is_bing,
                "have": have,
                "imgFile": _img_file(tmp_name, item_type),
            }
        )
        return True

    def lottery4(self) -> bool:
        gacha_def = meta.gacha_sim_config()["gacha"]
        tmp_chance4 = gacha_def["chance4"]
        counter = self.user[self.type]

        # 四星保底
        if counter["num4"] >= 9:
            tmp_chance4 += 10000
        elif counter["num4"] >= 5:
            tmp_chance4 += (counter["num4"] - 4) ** 2 * 500

        # 没抽中四星
        if self.rng(1, 10000) > tmp_chance4:
            counter["num4"] += 1
            return False

        # 保底四星数清零
        counter["num4"] = 0

        tmp_up = 75 if self.type == "weapon" else 50
        if counter["isUp4"] == 1:
            counter["isUp4"] = 0
            tmp_up = 100
        if self.type == "permanent":
            tmp_up = 0

        item_type = "role"
        # 当祈愿获取到4星物品时，有50%的概率为本期UP角色
        if self.rng(1, 100) <= tmp_up:
            # up 4星
            tmp_name = _sample(self.pool["up4"], self.rng)
            item_type = self.type
        else:
            counter["isUp4"] = 1
            # 一半概率武器 一半4星
            if self.rng(1, 100) <= 50:
                tmp_name = _sample(self.pool["role4"], self.rng)
                item_type = "role"
            else:
                tmp_name = _sample(self.pool["weapon4"], self.rng)
                item_type = "weapon"

        have = tmp_name in self.four_have
        if not have:
            self.four_have.append(tmp_name)

        self.res.append(
            {
                "name": tmp_name,
                "star": 4,
                "type": item_type,
                "num": None,
                "element": _element(tmp_name, item_type),
                "index": self.index,
                "isBigUP": False,
                "isBing": False,
                "imgFile": _img_file(tmp_name, item_type),
                "have": have,
            }
        )
        return True

    def lottery3(self) -> bool:
        tmp_name = _sample(self.pool["weapon3"], self.rng)
        self.res.append(
            {
                "name": tmp_name,
                "star": 3,
                "type": "weapon",
                "num": None,
                "element": _element(tmp_name, "weapon"),
                "index": self.index,
                "isBigUP": False,
                "isBing": False,
                "imgFile": _img_file(tmp_name, "weapon"),
                "have": False,
            }
        )
        return True


def lottery(user: dict[str, Any], pool: dict[str, Any], gacha_type: str, rng: Rng) -> list[dict[str, Any]]:
    """十连抽（不落地状态，落地由调用方负责）"""
    return _Lottery(user, pool, gacha_type, rng).run()


# ---------------------------------------------------------------------------
# 结果信息（对照 JS lotteryInfo）
# ---------------------------------------------------------------------------


def _short_name(name: str) -> str:
    """gsCfg.shortName 等价：优先角色 abbr，其次武器 abbr/名字"""
    char = meta.get_character(name, "gs")
    if char:
        return char.get("abbr") or char.name or ""
    weapon = meta.get_weapon(name, "gs")
    if weapon:
        return weapon.get("abbr") or weapon.get("name") or ""
    return ""


def lottery_info(
    user: dict[str, Any], res: list[dict[str, Any]], pool: dict[str, Any], gacha_type: str
) -> dict[str, Any]:
    """对照 JS lotteryInfo()"""
    info = f"累计「{user[gacha_type]['num5']}抽」"
    now_five = 0
    now_four = 0

    for v in res:
        if v["star"] == 5:
            now_five += 1
            if v["type"] == "role":
                char = meta.get_character(v["name"], "gs")
                info = (char.get("abbr") if char else "") or ""
            else:
                weapon = meta.get_weapon(v["name"], "gs")
                info = ((weapon or {}).get("abbr") or "") or ""
            info += f"「{v['num']}抽」"
            if v.get("isBigUP"):
                info += "大保底"
            if v.get("isBing"):
                info += "定轨"
        if v["star"] == 4:
            now_four += 1

    pool_name = "常驻池" if gacha_type == "permanent" else f"角色池：{_short_name(pool['up5'][0])}"

    return {
        "info": info,
        "nowFive": now_five,
        "nowFour": now_four,
        "poolName": pool_name,
        "isWeapon": gacha_type == "weapon",
        "bingWeapon": get_bing_weapon(pool, user, gacha_type, short_name=True),
        "lifeNum": user[gacha_type].get("lifeNum", 0),
    }


# ---------------------------------------------------------------------------
# 每日次数限制（对照 apps/gacha.js checkLimit）
# ---------------------------------------------------------------------------


def _default_daily_limit() -> int | None:
    """插件配置 miao_gacha_daily_limit；nonebot 未初始化时返回 None"""
    try:
        from nonebot import get_plugin_config

        from ..config import Config

        return get_plugin_config(Config).miao_gacha_daily_limit
    except Exception:
        return None


def check_limit(scope_key: str, gacha_type: str, is_master: bool = False, daily_limit: int | None = None) -> str | None:
    """超限返回提示文本（照 JS checkLimit 文案，不含群名片前缀），否则 None

    count/LimitSeparate 用 set.json 默认值，daily_limit（插件配置）覆盖 count
    """
    if is_master:
        return None

    user = load_user(scope_key)
    gacha_set = meta.gacha_sim_config()["set"]["default"]
    if daily_limit is None:
        daily_limit = _default_daily_limit()
    count = daily_limit if daily_limit is not None else gacha_set.get("count", 1)

    num = user["today"]["num"]
    weapon_num = user["today"]["weaponNum"]
    now_count = weapon_num if gacha_type == "weapon" else num

    if gacha_set.get("LimitSeparate", 0) == 1:
        if now_count < count * 10:
            return None
    elif num + weapon_num < count * 10:
        return None

    msg = ""
    stars = user["today"]["star"]
    if stars:
        msg += "今日五星："
        if len(stars) >= 4:
            msg += f"{len(stars)}个"
        else:
            msg += "\n".join(f"{s['name']}({s['num']})" for s in stars)
        if user["week"]["num"] >= 2:
            msg += f"\n本周：{user['week']['num']}个五星"
    else:
        msg += f"今日已抽，累计{now_count}抽无五星"
    return msg


# ---------------------------------------------------------------------------
# 定轨切换（对照 apps/gacha.js weaponBing）
# ---------------------------------------------------------------------------


def toggle_bing(scope_key: str) -> str:
    """定轨 type 1→2→0 循环，命定值清零，返回 JS 版文案"""
    user = load_user(scope_key)
    now_pool = get_now_pool()
    weapon_state = user["weapon"]

    if weapon_state["type"] >= 2:
        weapon_state["type"] = 0
        msg = "\n定轨已取消"
    else:
        weapon_state["type"] += 1
        lines = [
            f"[√] {name}" if weapon_state["type"] - 1 == i else f"[  ] {name}"
            for i, name in enumerate(now_pool["weapon5"])
        ]
        msg = "定轨成功\n" + "\n".join(lines)

    # 命定值清零
    weapon_state["lifeNum"] = 0
    save_user(scope_key, user)
    return msg


# ---------------------------------------------------------------------------
# 高层接口
# ---------------------------------------------------------------------------


def do_gacha(
    scope_key: str,
    kind: str,
    is_master: bool = False,
    daily_limit: int | None = None,
    rng: Rng | None = None,
) -> dict[str, Any]:
    """十连模拟抽卡。kind ∈ {"role","role2","weapon","permanent"}

    返回 {"code": "ok"|"limit", "msg"(limit 时), "list", "info", "nowFive",
    "nowFour", "poolName", "isWeapon", "bingWeapon", "lifeNum"}
    """
    gacha_type = _gacha_type(kind)

    limit_msg = check_limit(scope_key, gacha_type, is_master, daily_limit)
    if limit_msg is not None:
        return {"code": "limit", "msg": limit_msg}

    user = load_user(scope_key)
    pool = get_pool(kind)
    res = lottery(user, pool, gacha_type, rng or _default_rng)
    save_user(scope_key, user)

    return {"code": "ok", "list": res, **lottery_info(user, res, pool, gacha_type)}
