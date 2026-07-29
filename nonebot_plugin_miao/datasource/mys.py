"""米游社面板数据源：DS 签名 + MysApi 封装 + 原神/星铁面板映射 + update_profile_mys

出处：
- DS 签名与请求头：Miao-Yunzai plugins/genshin/model/mys/mysApi.js
  getDs（201-212 行，salt/t/r/md5 拼接）、getHeaders（168-199 行，x-rpc-* 头与 UA）、
  device（46-49 行，Yz- + md5(uid) 前 5 位）、getServer（65-98 行，uid 首段 → 区服）
- 接口 URL：Miao-Yunzai plugins/genshin/model/mys/apiTool.js getUrlMap
  （gs character 91-100 行 / sr avatarInfo 220-224 行，国服 api-takumi-record 域）
- retcode 处理：Miao-Yunzai plugins/genshin/model/mys/mysInfo.js checkCode（387-458 行）
- 面板映射：refs/miao-plugin models/serv/api/MysPanelData.js（原神）、
  MysPanelHSRData.js + MysPanelHSRApi.js（星铁）、MysPanelMappings.js / MysPanelHSRMappings.js
- 调用流程：refs/miao-plugin models/serv/ProfileReq.js requestProfile（96-112 行）：
  原神先 character/list 取全部角色 id 再 character/detail；星铁 avatarInfo 一次拿全

解析产物与 enka/mihomo 相同的统一 avatar 结构（见 core/player.py），可直接喂给
core/attr_calc.py 的 calc_attr。仅支持国服 UID（国际服走 hoyolab 域与另一套 salt，
本插件未接入）。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import time
from typing import Any

import httpx

from ..core import meta, store
from ..core.attr_calc import calc_promote
from ..core.player import Player
from .errors import ProfileError
from .mihomo import ENHANCED_CHAR_IDS, _normalize_trees
from .profile_service import _interval_seconds

# ---------------------------------------------------------------------------
# DS 签名（Miao-Yunzai mysApi.js getDs，201-212 行）
# ---------------------------------------------------------------------------

# salt：mysApi.js:204（国服）/ 206（国际服，本插件未使用，仅作记录）
SALT_CN = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
SALT_OS = "okr4obncj8bw5a65hbnn5oo6ixjc3l9w"

# 请求头常量（mysApi.js getHeaders 的 cn 分支，169-176 行）
APP_VERSION = "2.40.1"
CLIENT_TYPE = "5"
REFERER = "https://webstatic.mihoyo.com/"
# UA 中的设备号（mysApi.js:47）：Yz- + md5(uid) 前 5 位
UA_TEMPLATE = (
    "Mozilla/5.0 (Linux; Android 12; {device}) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/99.0.4844.73 Mobile Safari/537.36 miHoYoBBS/2.40.1"
)

TIMEOUT = 20

# 接口（apiTool.js getUrlMap 国服分支，hostRecord = api-takumi-record.mihoyo.com）
_HOST_RECORD = "https://api-takumi-record.mihoyo.com"
GS_CHARACTER_LIST_URL = f"{_HOST_RECORD}/game_record/app/genshin/api/character/list"  # apiTool.js:92-95
GS_CHARACTER_DETAIL_URL = f"{_HOST_RECORD}/game_record/app/genshin/api/character/detail"  # apiTool.js:97-100
SR_AVATAR_INFO_URL = f"{_HOST_RECORD}/game_record/app/hkrpg/api/avatar/info"  # apiTool.js:221-224

# 区服表（mysApi.js:6-17 game_region）
_GAME_REGION = {
    "gs": ["cn_gf01", "cn_qd01", "os_usa", "os_euro", "os_asia", "os_cht"],
    "sr": ["prod_gf_cn", "prod_qd_cn", "prod_official_usa", "prod_official_euro",
           "prod_official_asia", "prod_official_cht"],
}

_CN_SERVER_RE = re.compile(r"cn_|_cn")


def get_server(uid: str | int, game: str) -> str:
    """uid → 米游社区服（移植 mysApi.js getServer，65-98 行）"""
    regions = _GAME_REGION[game]
    head = str(uid)[:-8]  # uid 去掉后 8 位，剩首段
    index = {"5": 1, "6": 2, "7": 3, "8": 4, "18": 4, "9": 5}.get(head, 0)
    return regions[index]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _serialize_query(query: dict[str, Any] | str | None) -> str:
    """query → DS 用的 q 串：dict 按 key 排序拼接，str 原样透传"""
    if not query:
        return ""
    if isinstance(query, str):
        return query
    return "&".join(f"{k}={query[k]}" for k in sorted(query))


def _serialize_body(body: dict[str, Any] | str | None) -> str:
    """body → DS 用的 b 串：dict 按 JS JSON.stringify 序列化（紧凑、不转义非 ASCII）"""
    if not body:
        return ""
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def get_ds(
    query: dict[str, Any] | str | None = None,
    body: dict[str, Any] | str | None = None,
    salt: str = SALT_CN,
    with_b: bool = True,
    t: int | None = None,
    r: int | None = None,
) -> str:
    """生成 DS 头（移植 mysApi.js getDs）：md5(salt&t&r&b&q)，返回 "t,r,md5"

    t/r 可注入以便测试；默认取当前秒级时间戳与 6 位随机数（源码为
    Math.floor(Math.random() * 900000 + 100000)）。with_b=False 时 b 置空。
    """
    t = int(time.time()) if t is None else int(t)
    r = random.randint(100000, 999999) if r is None else int(r)
    q = _serialize_query(query)
    b = _serialize_body(body) if with_b else ""
    ds = _md5(f"salt={salt}&t={t}&r={r}&b={b}&q={q}")
    return f"{t},{r},{ds}"


# ---------------------------------------------------------------------------
# 米游社 API 封装
# ---------------------------------------------------------------------------


class MysApi:
    """米游社接口封装（对照 Miao-Yunzai mysApi.js + apiTool.js，只保留面板所需接口）"""

    def __init__(self, cookie: str, game: str = "gs"):
        if game not in ("gs", "sr"):
            raise ValueError(f"非法游戏标识: {game!r}，仅支持 ('gs', 'sr')")
        self.cookie = cookie
        self.game = game
        self._client = httpx.AsyncClient(timeout=TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------- 内部 ----------------

    def _server(self, uid: str | int) -> str:
        """区服判定，国际服暂不支持（国际服需 hoyolab 域 + SALT_OS + client_type=2）"""
        server = get_server(uid, self.game)
        if not _CN_SERVER_RE.search(server):
            raise ProfileError("米游社面板暂只支持国服 UID，国际服请使用 #更新面板", "mys")
        return server

    def _headers(self, uid: str | int, query: str = "", body: str = "") -> dict[str, str]:
        """请求头（移植 mysApi.js getHeaders）：x-rpc-* + DS + Cookie"""
        device = f"Yz-{_md5(str(uid))[:5]}"
        return {
            "x-rpc-app_version": APP_VERSION,
            "x-rpc-client_type": CLIENT_TYPE,
            "User-Agent": UA_TEMPLATE.format(device=device),
            "Referer": REFERER,
            "DS": get_ds(query, body),
            "Cookie": self.cookie,
        }

    @staticmethod
    def _check_retcode(res: dict[str, Any]) -> dict[str, Any]:
        """retcode 检查（对照 mysInfo.js checkCode），返回 data 载荷；失败抛 ProfileError"""
        retcode = int(res.get("retcode") if res.get("retcode") is not None else -1)
        message = str(res.get("message") or "")
        if retcode == 0:
            return res.get("data") or {}
        if retcode in (10104, 1008):
            raise ProfileError("米游社 cookie 无效或已过期，请重新发送 #绑定cookie 绑定", "mys")
        if retcode in (-1, -100, 1001, 10001, 10103):
            if re.search(r"登录|login", message, re.I):
                raise ProfileError("米游社 cookie 已失效，请重新发送 #绑定cookie 绑定", "mys")
            raise ProfileError(f"米游社接口报错：{message or retcode}", "mys")
        if retcode == 10101:
            raise ProfileError("米游社查询已达今日上限，请明天再试", "mys")
        if retcode == 10102:
            raise ProfileError("米游社数据未公开，请到米游社 App 打开【个人主页数据公开】", "mys")
        if retcode in (1034, 10035):
            raise ProfileError("米游社触发验证码，请稍后再试", "mys")
        raise ProfileError(f"米游社接口返回错误（retcode {retcode}）：{message or '未知错误'}", "mys")

    async def _request(
        self,
        uid: str | int,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """统一请求：拼接 query、签名、发请求、检查 retcode，返回 data 载荷"""
        # q/b 必须与实际发送内容逐字节一致，否则 DS 校验失败
        q = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        b = _serialize_body(body)
        headers = self._headers(uid, q, b)
        if b:
            headers["Content-Type"] = "application/json"
        try:
            resp = await self._client.request(
                method,
                f"{url}?{q}" if q else url,
                headers=headers,
                content=b.encode("utf-8") if b else None,
            )
        except httpx.HTTPError as e:
            raise ProfileError(f"请求米游社失败：{e}，请稍后重试", "mys") from e
        if resp.status_code != 200:
            raise ProfileError(f"米游社接口返回 HTTP {resp.status_code}，请稍后重试", "mys")
        try:
            res = resp.json()
        except ValueError as e:
            raise ProfileError("米游社返回了无法解析的数据，请稍后重试", "mys") from e
        return self._check_retcode(res)

    # ---------------- 接口 ----------------

    async def get_character_ids(self, uid: str | int) -> list[int]:
        """原神角色列表（character/list，POST），返回全部已拥有角色 id

        对照 ProfileReq.js:103-104：character.list 的 id 全量传给 character/detail。
        """
        server = self._server(uid)
        data = await self._request(uid, "POST", GS_CHARACTER_LIST_URL, body={"role_id": str(uid), "server": server})
        return [int(c["id"]) for c in data.get("list") or [] if c.get("id")]

    async def gs_panel(self, uid: str | int, character_ids: list[int]) -> dict[str, Any]:
        """原神面板详情（character/detail，POST），返回含 list 的 data 载荷"""
        server = self._server(uid)
        return await self._request(
            uid,
            "POST",
            GS_CHARACTER_DETAIL_URL,
            body={"role_id": str(uid), "server": server, "character_ids": [int(i) for i in character_ids]},
        )

    async def sr_panel(self, uid: str | int) -> dict[str, Any]:
        """星铁面板（avatar/info，GET，need_wiki=true），返回含 avatar_list 的 data 载荷"""
        server = self._server(uid)
        return await self._request(
            uid,
            "GET",
            SR_AVATAR_INFO_URL,
            params={"need_wiki": "true", "role_id": str(uid), "server": server},
        )


# ---------------------------------------------------------------------------
# 原神面板映射（移植 MysPanelData.js + MysPanelMappings.js）
# ---------------------------------------------------------------------------

# 主词条 property_type → mainId（MysPanelMappings.js artifactMainIdMapping）
_GS_MAIN_ID_MAP: dict[int, dict[int, int]] = {
    1: {2: 14001},  # 生命值
    2: {5: 12001},  # 攻击力
    3: {3: 10002, 6: 10004, 9: 10006, 23: 10007, 28: 10008},
    4: {3: 15002, 6: 15004, 9: 15006, 28: 15007, 30: 15015,
        40: 15008, 41: 15009, 42: 15011, 43: 15014, 44: 15012, 45: 15013, 46: 15010},
    5: {3: 13002, 6: 13004, 9: 13006, 20: 13007, 22: 13008, 26: 13009, 28: 13010},
}

# 副词条 property_type → attr key（MysPanelMappings.js propertyType2attrName）
_GS_PROP2ATTR = {
    6: "atk", 5: "atkPlus", 3: "hp", 2: "hpPlus", 9: "def", 8: "defPlus",
    20: "cpct", 22: "cdmg", 23: "recharge", 28: "mastery",
}

# 固定值词条（其余为百分比，MysPanelMappings.js fixedAttrNames）
_GS_FIXED_ATTRS = ("hpPlus", "defPlus", "mastery", "atkPlus")

_GS_TALENT_KEYS = ("a", "e", "q")
# 米游社 selected_properties 中角色基础生命的 property_type（MysPanelData.js:19）
_GS_HP_PROPERTY_TYPE = 2000

_FLOAT_RE = re.compile(r"^[ \t]*[+-]?(\d+\.?\d*|\.\d+)")


def _parse_float_js(value: Any) -> float:
    """模拟 JS parseFloat：取字符串前导浮点部分（"5.8%" → 5.8），失败返回 0"""
    if isinstance(value, (int, float)):
        return float(value)
    m = _FLOAT_RE.match(str(value or ""))
    return float(m.group(0)) if m else 0.0


def _gs_elem(element: Any) -> str:
    """米游社中文元素名 → 元素 key（对照 Format.elem，映射表见 core/attr_calc._GS_ELEM_ALIAS）"""
    from ..core.attr_calc import _ELEM_MAP

    return _ELEM_MAP["gs"].get(str(element or "").strip().lower(), "")


def _gs_char_hp(char: meta.CharacterMeta, level: int, promote: int) -> float | None:
    """角色裸生命（meta attr details 按突破段插值，对照 MysPanelData.js getCharHp 36-44 行）"""
    lv_step = [1, 20, 40, 50, 60, 70, 80, 90, 100]
    lv_left = lv_right = 0
    curr = 0
    for idx in range(len(lv_step) - 1):
        if curr == promote and lv_step[idx] <= level <= lv_step[idx + 1]:
            lv_left, lv_right = lv_step[idx], lv_step[idx + 1]
            break
        curr += 1
    details = (char.get("attr") or {}).get("details") or {}
    left = details.get(f"{lv_left}+") or details.get(str(lv_left))
    right = details.get(str(lv_right))
    if not left or not right:
        return None
    return float(left[0]) + (float(right[0]) - float(left[0])) * (level - lv_left) / (lv_right - lv_left)


def _gs_fix_promote(char: meta.CharacterMeta, level: int, selected_properties: list[dict[str, Any]]) -> int:
    """突破等级校正（移植 MysPanelData.js setAvatar 15-54 行）

    米游社只给等级不给突破数；等级恰在突破边界（20/40/.../80）时，
    用 selected_properties 里的角色基础生命与 meta 插值对比，判断是未突破还是已突破。
    """
    promote = calc_promote(level, "gs")
    if level in (20, 40, 50, 60, 70, 80):
        char_hp = 0.0
        for p in selected_properties:
            if p.get("property_type") == _GS_HP_PROPERTY_TYPE:
                char_hp = _parse_float_js(p.get("base"))
        if char_hp > 0:
            hp1 = _gs_char_hp(char, level, promote)
            hp2 = _gs_char_hp(char, level, promote + 1)
            if hp1 is not None and hp2 is not None and abs(char_hp - hp2) < abs(char_hp - hp1):
                promote += 1
    return min(promote, 6)


def _gs_talent(char: meta.CharacterMeta, cons: int, skills: list[dict[str, Any]]) -> dict[str, int]:
    """天赋映射（移植 MysPanelData.js getTalent）：skill_id 查 meta talentId，
    未命中的主动技能（skill_type==1）按顺序回退 a/e/q；最后按命座扣减展示等级"""
    talent_id = char.get("talentId") or {}
    talent_cons = char.get("talentCons") or {}
    idx = 0
    ret: dict[str, int] = {}
    for skill in skills:
        sid = str(skill.get("skill_id"))
        level = int(skill.get("level") or 0)
        if sid in talent_id:
            ret[talent_id[sid]] = level
        elif skill.get("skill_type") == 1 and idx < len(_GS_TALENT_KEYS):
            key = _GS_TALENT_KEYS[idx]
            idx += 1
            ret[key] = ret.get(key) or level
    # 命座加成扣减（cons>=3 时 talentCons 命中的天赋展示等级 -3）
    if cons >= 3:
        for key, lv in talent_cons.items():
            if lv and ret.get(key) and cons >= int(lv):
                ret[key] = max(1, ret[key] - 3)
    return ret


def _gs_weapon(data: dict[str, Any]) -> dict[str, Any]:
    """武器映射（移植 MysPanelData.js getWeapon，补充 id 字段以对齐统一结构）"""
    if not data:
        return {}
    w_meta = meta.get_weapon_by_id(data.get("id"), "gs")
    return {
        "id": data.get("id"),
        "name": w_meta["name"] if w_meta else (data.get("name") or ""),
        "level": int(data.get("level") or 1),
        "promote": int(data.get("promote_level") or 0),
        "affix": int(data.get("affix_level") or 1),
    }


def _gs_sub_attr_combination(rarity: int, times: int, property_type: int, value: Any) -> list[str]:
    """副词条反推（移植 MysPanelData.js getArtifactAttrIdCombination）

    米游社只给属性、强化次数与最终值，没有词条 id；在 attrIdMap 中找同星级同属性
    的所有单条数值，穷举 times+1 次组合，取与最终值误差最小的一组词条 id。
    """
    attr_name = _GS_PROP2ATTR.get(property_type)
    if not attr_name:
        return []
    if attr_name in _GS_FIXED_ATTRS:
        dest = _parse_float_js(value)
    else:
        # 百分比词条值为 "5.8%" 形式，attrIdMap 中存的是小数
        dest = _parse_float_js(value) * 0.01
    attr_id_map = meta.artifact_extra("gs").get("attrIdMap") or {}
    cur_values = [
        (float(cfg["value"]), aid)
        for aid, cfg in attr_id_map.items()
        if aid.startswith(str(rarity)) and cfg.get("key") == attr_name
    ]
    if not cur_values:
        return []
    best_err = float("inf")
    best: tuple[str, ...] = ()
    for combo in itertools.product(cur_values, repeat=times + 1):
        err = abs(sum(v for v, _ in combo) - dest)
        if err < best_err:
            best_err = err
            best = tuple(aid for _, aid in combo)
    return list(best)


def _gs_artis(relics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """圣遗物映射（移植 MysPanelData.js getArtifact/getArtifactAttrIds），key 为部位 1-5"""
    ret: dict[str, dict[str, Any]] = {}
    for relic in relics:
        idx = int(relic.get("pos") or 0)
        if not 1 <= idx <= 5:
            continue
        rarity = int(relic.get("rarity") or 5)
        main_property = relic.get("main_property") or {}
        attr_ids: list[str] = []
        for sub in relic.get("sub_property_list") or []:
            attr_ids.extend(_gs_sub_attr_combination(
                rarity,
                int(sub.get("times") or 0),
                int(sub.get("property_type") or 0),
                sub.get("value"),
            ))
        ret[str(idx)] = {
            "id": relic.get("id"),
            "level": min(20, int(relic.get("level") or 0)),
            "star": rarity,
            "mainId": (_GS_MAIN_ID_MAP.get(idx) or {}).get(main_property.get("property_type")),
            "attrIds": attr_ids,
        }
    return ret


def _parse_gs_avatar(ds: dict[str, Any]) -> dict[str, Any] | None:
    """解析单个角色（移植 MysPanelData.js setAvatar），meta 查不到的角色跳过"""
    base = ds.get("base") or {}
    char = meta.get_character(base.get("id"), "gs")
    if not char:
        return None
    level = int(base.get("level") or 1)
    cons = int(base.get("actived_constellation_num") or 0)
    costumes = ds.get("costumes") or []
    costume_id = costumes[0].get("id") if costumes else 0
    costume = int(costume_id) if costume_id and int(costume_id) in (char.get("costume") or []) else 0
    return {
        "id": char.id,
        "name": char.name,
        "elem": _gs_elem(base.get("element")) or char.get("elem") or "",
        "level": level,
        "promote": _gs_fix_promote(char, level, ds.get("selected_properties") or []),
        "cons": cons,
        "fetter": int(base.get("fetter") or 0),
        "costume": costume,
        "talent": _gs_talent(char, cons, ds.get("skills") or []),
        "weapon": _gs_weapon(ds.get("weapon") or {}),
        "artis": _gs_artis(ds.get("relics") or []),
        "_source": "mys",
        "_time": int(time.time()),
    }


def parse_gs_panel(raw: dict[str, Any], uid: str | int) -> dict[str, Any]:
    """character/detail 的 data 载荷 → Player 结构（avatars + dataSource=mys）

    米游社面板接口不含玩家昵称/等级等基础信息，只产出 avatars
    （基础信息保留本地已有值，对齐 miao MysPanelApi.updatePlayer 只更角色的行为）。
    """
    items = raw.get("list") or []
    if not items:
        raise ProfileError("米游社未返回角色数据，请确认已在米游社绑定角色并公开数据", "mys")
    avatars: dict[str, dict[str, Any]] = {}
    for ds in items:
        avatar = _parse_gs_avatar(ds)
        if avatar:
            avatars[str(avatar["id"])] = avatar
    if not avatars:
        raise ProfileError("米游社角色数据均无法识别，请稍后重试", "mys")
    return {
        "uid": str(uid),
        "avatars": avatars,
        "dataSource": "mys",
        "updateTime": int(time.time()),
        # 对齐 MysPanelApi.js cdTime：接口不返回 ttl，固定 60 秒
        "ttl": 60,
    }


# ---------------------------------------------------------------------------
# 星铁面板映射（移植 MysPanelHSRData.js / MysPanelHSRApi.js / MysPanelHSRMappings.js）
# ---------------------------------------------------------------------------

# property_type → attr key（MysPanelHSRMappings.js propertyType2attrName）
_SR_PROP2ATTR = {
    1: "hpPlus", 2: "atkPlus", 3: "defPlus", 4: "speed", 5: "cpct", 6: "cdmg",
    7: "heal", 9: "recharge", 10: "effPct", 11: "effDef", 12: "phy",
    14: "fire", 16: "ice", 18: "elec", 20: "wind", 22: "quantum", 24: "imaginary",
    32: "hp", 33: "atk", 34: "def", 58: "stance",
    # 主词条另一套编号（源码注释：有些属性就是有两个值）
    51: "speed", 52: "cpct", 53: "cdmg", 27: "hpPlus", 29: "atkPlus", 31: "defPlus",
    54: "recharge", 55: "heal", 56: "effPct", 57: "effDef", 59: "stance",
}

# remake 文本 → 天赋 key（MysPanelHSRData.js getTalent remakeMap）
_SR_REMAKE_MAP = {
    "普攻": "a", "战技": "e", "终结技": "q", "天赋": "t", "秘技": "z",
    "欢愉技": "xe", "忆灵技": "me", "忆灵天赋": "mt",
}
# 命座对天赋的加成步数（MysPanelHSRData.js:170）
_SR_TALENT_CONS_STEP = {"a": 1, "e": 2, "q": 2, "t": 2, "me": 1, "mt": 1, "xe": 1}

# properties 中基础生命/速度的 property_type（MysPanelHSRData.js:22, 89）
_SR_HP_PROPERTY_TYPE = 1
_SR_SPEED_PROPERTY_TYPE = 4


def _sr_char_hp(char: meta.CharacterMeta, level: int, promote: int) -> float:
    """角色裸生命（对照 Character.getLvAttr：attr[promote].attrs + grow*(level-1)）"""
    lv_attr = (char.get("attr") or {}).get(str(promote)) or {}
    hp = float((lv_attr.get("attrs") or {}).get("hp") or 0)
    hp += float((lv_attr.get("grow") or {}).get("hp") or 0) * (level - 1)
    return hp


def _sr_weapon_hp(w_meta: dict[str, Any], level: int, promote: int) -> float:
    """光锥生命（对照 Weapon.calcAttr 星铁分支：attr[promote].attrs + growAttr*(level-1)）"""
    lv_attr = (w_meta.get("attr") or {}).get(str(promote)) or {}
    hp = float((lv_attr.get("attrs") or {}).get("hp") or 0)
    hp += float((w_meta.get("growAttr") or {}).get("hp") or 0) * (level - 1)
    return hp


def _sr_fix_promote(
    char: meta.CharacterMeta, level: int, equip: dict[str, Any] | None, properties: list[dict[str, Any]]
) -> tuple[int, int | None]:
    """突破等级校正（移植 MysPanelHSRData.js setAvatar 16-69 行）

    等级/光锥等级恰在突破边界时，用 properties 里的基础生命与
    （角色 + 光锥）候选突破组合对比，取误差最小的一组。返回 (角色突破, 光锥突破)。
    """
    promote = calc_promote(level, "sr")
    weapon_promote = calc_promote(int(equip["level"]), "sr") if equip and equip.get("level") else None
    boundary = level in (20, 30, 40, 50, 60, 70)
    w_boundary = bool(equip and int(equip.get("level") or 0) in (20, 30, 40, 50, 60, 70))
    if boundary or w_boundary:
        base_hp = 0.0
        for p in properties:
            if p.get("property_type") == _SR_HP_PROPERTY_TYPE:
                base_hp = _parse_float_js(p.get("base"))
        if base_hp > 0:
            w_meta = meta.get_weapon_by_id(equip.get("id"), "sr") if equip else None
            if not equip or w_meta:
                char_promotes = [promote + 1, promote] if boundary else [promote]
                if w_meta:
                    weapon_promotes = [weapon_promote + 1, weapon_promote] if w_boundary else [weapon_promote]
                else:
                    weapon_promotes = [None]
                min_diff = float("inf")
                best_cp, best_wp = promote, weapon_promote
                for cp in char_promotes:
                    for wp in weapon_promotes:
                        hp = _sr_char_hp(char, level, cp)
                        if w_meta and wp is not None:
                            hp += _sr_weapon_hp(w_meta, int(equip["level"]), wp)
                        diff = abs(base_hp - hp)
                        if diff < min_diff:
                            min_diff = diff
                            best_cp = cp
                            if w_meta:
                                best_wp = wp
                promote, weapon_promote = best_cp, best_wp
    return min(promote, 6), (min(weapon_promote, 6) if weapon_promote is not None else None)


def _sr_talent(
    char: meta.CharacterMeta, cons: int, skills: list[dict[str, Any]], servant_skills: list[dict[str, Any]]
) -> dict[str, int]:
    """天赋映射（移植 MysPanelHSRData.js getTalent）：按 remake 文本归 key，命座加成扣减"""
    talent_cons = char.get("talentCons") or {}
    ret: dict[str, int] = {}
    for skill in itertools.chain(skills, servant_skills or []):
        key = _SR_REMAKE_MAP.get(skill.get("remake") or "")
        if key:
            ret[key] = int(skill.get("level") or 0)
    # 命座加成扣减（talentCons 可能是 {key: cons 等级} 或 {key: [cons 等级列表]}）
    for key, lv in talent_cons.items():
        add_num = _SR_TALENT_CONS_STEP.get(key, 0)
        if isinstance(lv, list):
            plus = sum(1 for c in lv if cons >= int(c)) * add_num
        elif isinstance(lv, (int, float)) and lv > 0 and cons >= int(lv):
            plus = add_num
        else:
            # 部分角色 talentCons 混有 {cons等级: key} 反向条目（值为字母 key），JS 中 lv>0 为 false 跳过
            plus = 0
        if plus > 0 and ret.get(key):
            ret[key] = max(1, ret[key] - plus)
    return ret


def _sr_weapon(equip: dict[str, Any], promote: int | None) -> dict[str, Any]:
    """光锥映射（移植 MysPanelHSRData.js getWeapon，补充 name 字段以对齐统一结构）"""
    w_meta = meta.get_weapon_by_id(equip.get("id"), "sr")
    return {
        "id": equip.get("id"),
        "name": w_meta["name"] if w_meta else "",
        "level": int(equip.get("level") or 1),
        "promote": min(int(promote or 0), 6),
        "affix": int(equip.get("rank") or 1),
    }


def _sr_main_id(pos: int, main_property: dict[str, Any]) -> int | None:
    """主词条 property_type → mainId（移植 MysPanelHSRData.js getArtifactMainId：反转 meta mainIdx）"""
    prop_name = _SR_PROP2ATTR.get(main_property.get("property_type"))
    if not prop_name:
        return None
    main_idx = (meta.artifact_star_meta("sr").get("mainIdx") or {}).get(str(pos)) or {}
    name2id = {v: k for k, v in main_idx.items()}
    return int(name2id[prop_name]) if prop_name in name2id else None


def _sr_sub_attr_id(rarity: int, times: int, property_type: int, value: Any) -> str | None:
    """副词条反推（移植 MysPanelHSRData.js getArtifactAttrId）

    返回 "propertyId,times,numSteps"（与 mihomo 的 "affixId,cnt,step" 落盘格式一致）。
    源码中 valueStr.substring(-1) 恒为整串（JS substring 负参归零），百分比判断实际不生效，
    百分值按 parseFloat 语义直接取数字部分（与 starData.sub 的 base/step 同量纲）。
    """
    prop_name = _SR_PROP2ATTR.get(property_type)
    if not prop_name:
        return None
    star_data = (meta.artifact_star_meta("sr").get("starData") or {}).get(str(rarity)) or {}
    sub_attr = star_data.get("sub") or {}
    prop_id = next((pid for pid, cfg in sub_attr.items() if cfg.get("key") == prop_name), None)
    if prop_id is None:
        return None
    cfg = sub_attr[prop_id]
    dest = _parse_float_js(value)
    num_steps = round((dest - times * float(cfg["base"])) / float(cfg["step"])) if cfg.get("step") else 0
    return f"{prop_id},{times},{num_steps}"


def _sr_artis(relics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """遗器映射（移植 MysPanelHSRData.js getArtifact：relics + ornaments 合并），key 为部位 1-6"""
    ret: dict[str, dict[str, Any]] = {}
    for relic in relics:
        idx = int(relic.get("pos") or 0)
        if not 1 <= idx <= 6:
            continue
        rarity = int(relic.get("rarity") or 5)
        attr_ids: list[str] = []
        for sub in relic.get("properties") or []:
            if sub.get("is_preview"):
                continue
            attr_id = _sr_sub_attr_id(
                rarity,
                int(sub.get("times") or 0),
                int(sub.get("property_type") or 0),
                sub.get("value"),
            )
            if attr_id:
                attr_ids.append(attr_id)
        ret[str(idx)] = {
            "id": relic.get("id"),
            "level": min(15, int(relic.get("level") or 0)),
            "star": rarity,
            "mainId": _sr_main_id(idx, relic.get("main_property") or {}),
            "attrIds": attr_ids,
        }
    return ret


def _sr_enhanced_remap(ds: dict[str, Any]) -> dict[str, Any]:
    """加强角色 id 重映射（移植 MysPanelHSRApi.js updatePlayer 27-52 行）

    加强角色的 skills 里存在 "1{原id}" 前缀的 point_id，命中则归入 2 开头的新 id。
    """
    cid = ds.get("id")
    if cid not in ENHANCED_CHAR_IDS or not ds.get("skills"):
        return ds
    old = str(cid)
    if not any(str(s.get("point_id")).startswith("1" + old) for s in ds["skills"]):
        return ds
    ds = json.loads(json.dumps(ds))  # deepcopy
    new_id = int("2" + old[1:])
    ds["id"] = new_id
    for skill in ds.get("skills") or []:
        pid = str(skill.get("point_id"))
        if pid.startswith("1" + old):
            skill["point_id"] = int(pid.replace("1" + old, str(new_id), 1))
    return ds


def _sr_fix_speed(avatar: dict[str, Any], speed_final: float) -> None:
    """速度整数对齐（移植 MysPanelHSRData.js setAvatar 89-122 行）

    米游社面板速度为整数对齐值；计算速度与目标差 >0.2 时，给遗器速度副词条
    （id 前缀 "7,"）逐条 +1 步直到补足差额。计算失败时静默跳过（不影响解析）。
    """
    from ..core.attr_calc import calc_attr

    try:
        current = calc_attr(avatar, "sr").get("speed") or 0
    except Exception:
        return
    diff = speed_final - current
    if diff <= 0.2:
        return
    loop = 0
    while diff > 0.05 and loop < 20:
        has_speed = False
        for piece in (avatar.get("artis") or {}).values():
            if diff <= 0.05:
                break
            attr_ids = piece.get("attrIds") or []
            for i, attr in enumerate(attr_ids):
                if diff <= 0.05:
                    break
                if str(attr).startswith("7,"):
                    pid, count, step = str(attr).split(",")
                    attr_ids[i] = f"{pid},{count},{int(step) + 1}"
                    diff -= 0.3 if int(piece.get("star") or 5) >= 5 else 0.2
                    has_speed = True
        if not has_speed:
            break
        loop += 1


def _parse_sr_avatar(ds0: dict[str, Any]) -> dict[str, Any] | None:
    """解析单个角色（移植 MysPanelHSRData.js setAvatar），meta 查不到的角色跳过"""
    ds = _sr_enhanced_remap(ds0)
    char = meta.get_character(ds.get("id"), "sr")
    if not char:
        return None
    level = int(ds.get("level") or 1)
    equip = ds.get("equip") or None
    properties = ds.get("properties") or []
    promote, weapon_promote = _sr_fix_promote(char, level, equip, properties)
    cons = int(ds.get("rank") or 0)
    tree_ids = [
        s.get("point_id")
        for s in ds.get("skills") or []
        if s.get("point_type") != 2 and s.get("is_activated")
    ]
    avatar: dict[str, Any] = {
        "id": char.id,
        "name": char.name,
        "elem": char.get("elem") or "",
        "level": level,
        "promote": promote,
        "cons": cons,
        "talent": _sr_talent(char, cons, ds.get("skills") or [],
                             (ds.get("servant_detail") or {}).get("servant_skills") or []),
        "trees": _normalize_trees(tree_ids, char),
        "weapon": _sr_weapon(equip, weapon_promote) if equip else {},
        "artis": _sr_artis([*(ds.get("relics") or []), *(ds.get("ornaments") or [])]),
        "_source": "mys",
        "_time": int(time.time()),
    }
    for p in properties:
        if p.get("property_type") == _SR_SPEED_PROPERTY_TYPE:
            _sr_fix_speed(avatar, _parse_float_js(p.get("final")))
            break
    return avatar


def parse_sr_panel(raw: dict[str, Any], uid: str | int) -> dict[str, Any]:
    """avatar/info 的 data 载荷 → Player 结构（avatars + dataSource=mys）"""
    items = raw.get("avatar_list") or []
    if not items:
        raise ProfileError("米游社未返回角色数据，请确认已在米游社绑定角色并公开数据", "mys")
    avatars: dict[str, dict[str, Any]] = {}
    for ds in items:
        avatar = _parse_sr_avatar(ds)
        if avatar:
            avatars[str(avatar["id"])] = avatar
    if not avatars:
        raise ProfileError("米游社角色数据均无法识别，请稍后重试", "mys")
    return {
        "uid": str(uid),
        "avatars": avatars,
        "dataSource": "mys",
        "updateTime": int(time.time()),
        "ttl": 60,
    }


# ---------------------------------------------------------------------------
# 面板更新服务
# ---------------------------------------------------------------------------


def _global_cookie() -> str:
    """全局兜底 cookie（config.miao_mys_cookie），nonebot 未初始化时视为空"""
    try:
        from nonebot import get_plugin_config

        from ..config import Config

        return get_plugin_config(Config).miao_mys_cookie or ""
    except Exception:
        return ""


async def update_profile_mys(user_id: str | int, uid: str | int, game: str) -> dict[str, Any]:
    """米游社面板更新：cookie 检查 → CD 检查 → 抓取 → 解析 → 合并落盘 → 写 CD

    cookie 优先级：用户绑定 cookie > 全局配置 cookie。
    返回：
      - {"code": "no_cookie", "msg": 提示文案}：未绑定 cookie
      - {"code": "cd", "wait": 剩余秒数}：冷却中，未发起请求
      - {"code": "ok", "player": Player, "new_chars": [本次更新的角色名]}
    数据源错误抛 ProfileError（中文消息，命令层直接提示）。
    """
    store._check_game(game)
    uid = str(uid)
    cookie = store.get_cookie(user_id) or _global_cookie()
    if not cookie:
        return {"code": "no_cookie", "msg": "米游社面板需要绑定米游社 cookie，请先发送 #绑定cookie"}
    # CD 与 #更新面板 相互独立（不同数据源），key 加 mys 前缀
    cd_key = f"profile:mys:{game}:{uid}"
    wait = store.check_cd(cd_key, _interval_seconds())
    if wait > 0:
        return {"code": "cd", "wait": wait}
    api = MysApi(cookie, game)
    try:
        if game == "gs":
            character_ids = await api.get_character_ids(uid)
            parsed = parse_gs_panel(await api.gs_panel(uid, character_ids), uid)
        else:
            parsed = parse_sr_panel(await api.sr_panel(uid), uid)
    finally:
        await api.aclose()
    player = Player.load(uid, game)
    player.update(parsed)
    player.save()
    store.set_cd(cd_key, max(_interval_seconds(), int(parsed.get("ttl") or 0)))
    new_chars = [avatar.get("name") or aid for aid, avatar in (parsed.get("avatars") or {}).items()]
    return {"code": "ok", "player": player, "new_chars": new_chars}
