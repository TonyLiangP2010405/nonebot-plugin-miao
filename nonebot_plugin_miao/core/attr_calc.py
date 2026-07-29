"""面板属性计算：移植 refs/miao-plugin 的
- models/attr/Attr.js（calc/setCharAttr/setWeaponAttr/setArtisAttr/calcArtisAttr）
- models/attr/AttrData.js（属性容器：base/plus/pct 与白值统计）
- models/Weapon.js（calcAttr 白值/副词条按等级插值、getWeaponAffixBuffs 精炼静态词条）
- models/artis/ArtisAttr.js（mainId/attrIds → 实际词条数值，gs 与 sr 两套格式）

输入为 datasource 解析出的 avatar dict（见 core/player.py 注释），
输出对齐 JS 版 AttrData.getAttr()：各属性 computed 值 + hpBase/atkBase/defBase(/speedBase)
+ staticAttr（{key: {base, plus, pct}} 原始累加值，面板图展示 base+plus 用）。
JS 侧全程不做数值修约（Format.comma 仅用于展示），本模块同样保留原始浮点值。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from . import meta
from .jseval import eval_js

# ---------------------------------------------------------------------------
# AttrData.js：baseAttr 与 key 正则
# ---------------------------------------------------------------------------

BASE_ATTR = {
    "gs": "atk,def,hp,mastery,recharge,cpct,cdmg,dmg,phy,heal,shield,coloringDmg".split(","),
    "sr": "atk,def,hp,speed,recharge,cpct,cdmg,dmg,heal,stance,effPct,effDef,joy".split(","),
}

_ATTR_RE = {g: re.compile(rf"^({'|'.join(keys)})(Base|Plus|Pct|Inc)$") for g, keys in BASE_ATTR.items()}

# ---------------------------------------------------------------------------
# components/common/Elem.js：元素判定
# ---------------------------------------------------------------------------

_GS_ELEM_ALIAS = {
    "anemo": "风,蒙德",
    "geo": "岩,璃月",
    "electro": "雷,电,雷电,稻妻",
    "dendro": "草,须弥",
    "pyro": "火,纳塔",
    "hydro": "水,枫丹",
    "cryo": "冰,至冬",
}
_SR_ELEM_ALIAS = {
    "fire": "火",
    "ice": "冰",
    "wind": "风",
    "elec": "雷",
    "phy": "物理",
    "quantum": "量子",
    "imaginary": "虚数",
}


def _build_elem_map(game: str) -> dict[str, str]:
    alias = _GS_ELEM_ALIAS if game == "gs" else _SR_ELEM_ALIAS
    ret: dict[str, str] = {}
    for key, txt in alias.items():
        ret[key] = key
        for t in txt.split(","):
            ret[t] = key
    return ret


_ELEM_MAP = {g: _build_elem_map(g) for g in ("gs", "sr")}


def is_elem(key: str | None, game: str = "gs") -> bool:
    """Format.isElem：key 是否为元素名（英文 key 或中文名均可）"""
    return bool(key) and key in _ELEM_MAP[game]


def same_elem(key1: str | None, key2: str | None, game: str = "gs") -> bool:
    """Format.sameElem：两个元素名是否同元素"""
    return _ELEM_MAP[game].get(key1 or "") == _ELEM_MAP[game].get(key2 or "")


def elem_name(key: str | None, game: str = "gs") -> str:
    """元素 key → 中文名（gs 取别名首字，sr 取别名本身）"""
    if not key:
        return ""
    alias = _GS_ELEM_ALIAS if game == "gs" else _SR_ELEM_ALIAS
    std = _ELEM_MAP[game].get(key, key)
    txt = alias.get(std, "")
    return txt[0] if game == "gs" and txt else txt


# ---------------------------------------------------------------------------
# AttrData.js：属性容器
# ---------------------------------------------------------------------------


class AttrData:
    """移植 AttrData.js：_attr {key: {base, plus, pct}} + _base 白值统计"""

    def __init__(self, game: str, char_elem: str = ""):
        self.game = game
        self.char_elem = char_elem
        keys = BASE_ATTR[game]
        self._attr: dict[str, dict[str, float]] = {k: {"base": 0.0, "plus": 0.0, "pct": 0.0} for k in keys}
        self._base: dict[str, float] = {k: 0.0 for k in keys}

    def value(self, key: str) -> float:
        """对应 JS 的 _get：基础属性返回 base*(1+pct/100)+plus，组合 key 取单项"""
        if key in self._attr:
            a = self._attr[key]
            return a["base"] * (1 + a["pct"] / 100) + a["plus"]
        m = _ATTR_RE[self.game].match(key or "")
        if m:
            return self._attr[m.group(1)].get(m.group(2).lower(), 0.0)
        return 0.0

    def add(self, key: str | None, val: Any, is_base: bool = False) -> bool:
        """对应 JS 的 addAttr"""
        if not key:
            return False
        val = float(val)
        # 星铁元素伤词条：与角色同元素时归入 dmg，异色丢弃（AttrData.addAttr）
        if self.game == "sr" and is_elem(key, "sr"):
            if same_elem(self.char_elem, key, "sr"):
                key = "dmg"
        if key in self._attr:
            self._attr[key]["plus"] += val
            if is_base:
                self._base[key] += val
            return True
        m = _ATTR_RE[self.game].match(key)
        if m:
            k1, k2 = m.group(1), m.group(2).lower()
            self._attr[k1][k2] = self._attr[k1].get(k2, 0.0) + val
            if k2 == "base" or is_base:
                self._base[k1] += val
            return True
        return False

    def get_attr(self) -> dict[str, Any]:
        """对应 JS 的 getAttr：computed 值 + {hp,atk,def,speed}Base + staticAttr"""
        ret: dict[str, Any] = {}
        for key in BASE_ATTR[self.game]:
            ret[key] = self.value(key)
            if key in ("hp", "atk", "def", "speed"):
                ret[f"{key}Base"] = self.value(f"{key}Base")
        ret["_calc"] = True
        ret["staticAttr"] = self._attr
        return ret

    def get_base(self) -> dict[str, float]:
        return self._base


# ---------------------------------------------------------------------------
# 等级/突破
# ---------------------------------------------------------------------------

_LV_STEP = {"gs": [1, 20, 40, 50, 60, 70, 80, 90, 100], "sr": [1, 20, 30, 40, 50, 60, 70, 80]}


def calc_promote(lv: int, game: str = "gs") -> int:
    """移植 Attr.calcPromote：按等级反推突破数"""
    lvs = _LV_STEP[game]
    promote = 0
    for idx in range(len(lvs) - 1):
        if lvs[idx] <= lv <= lvs[idx + 1]:
            return promote
        promote += 1
    return promote


def _lv_segment(level: int, promote: int, lv_step: list[int]) -> tuple[int, int]:
    """移植 Attr.setCharAttr 的 lvLeft/lvRight 查找（promote 段内）"""
    lv_left = lv_right = 0
    curr = 0
    for idx in range(len(lv_step) - 1):
        if curr == promote:
            if lv_step[idx] <= level <= lv_step[idx + 1]:
                lv_left, lv_right = lv_step[idx], lv_step[idx + 1]
                break
        curr += 1
    return lv_left, lv_right


# ---------------------------------------------------------------------------
# 角色属性（Attr.setCharAttr）
# ---------------------------------------------------------------------------

# Attr.js 特有角色自带属性
_CHAR_SELF_ATTRS = {
    10000119: {"mastery": 200},  # 菈乌玛: +200 元素精通
    10000122: {"mastery": 100},  # 奈芙尔: +100 元素精通
}


def _set_char_attr(ad: AttrData, avatar: dict[str, Any], char: meta.CharacterMeta, game: str) -> None:
    level = int(avatar.get("level") or 1)
    promote = int(avatar.get("promote") or 0)
    if game == "sr":
        # Character.getLvAttr：attrs + grow*(level-1)
        char_attr = char.get("attr") or {}
        # 新版星铁角色 attr 为按突破段索引的数组（原版 metaAttr[promote] 对 dict/数组都成立）
        if isinstance(char_attr, list):
            lv_attr = char_attr[promote] if 0 <= promote < len(char_attr) else {}
        else:
            lv_attr = char_attr.get(str(promote)) or {}
        ret: dict[str, float] = {}
        for k, v in (lv_attr.get("attrs") or {}).items():
            ret[k] = float(v)
        for k, v in (lv_attr.get("grow") or {}).items():
            ret[k] = ret.get(k, 0.0) + float(v) * (level - 1)
        for k, v in ret.items():
            ad.add(k + ("Base" if k in ("hp", "atk", "def", "speed") else ""), v, True)
        # 行迹属性加成（Attr.setCharAttr sr 分支）
        tree = char.get("tree") or {}
        for tid in avatar.get("trees") or []:
            t_cfg = tree.get(str(tid))
            if t_cfg:
                key = t_cfg.get("key") or ""
                if key in ("atk", "hp", "def"):
                    key += "Pct"
                ad.add(key, t_cfg.get("value") or 0)
        return

    # 原神：attr.keys/details 按突破段插值
    meta_attr = char.get("attr") or {}
    keys = meta_attr.get("keys") or []
    details = meta_attr.get("details") or {}
    lv_left, lv_right = _lv_segment(level, promote, _LV_STEP["gs"])
    detail_left = details.get(f"{lv_left}+") or details.get(str(lv_left)) or []
    detail_right = details.get(str(lv_right)) or []

    def lv_data(i: int, step: bool = False) -> float:
        v_left = float(detail_left[i])
        v_right = float(detail_right[i])
        if not step:
            return v_left + (v_right - v_left) * (level - lv_left) / (lv_right - lv_left)
        return v_left + (v_right - v_left) * math.floor((level - lv_left) / 5) / round((lv_right - lv_left) / 5)

    ad.add("hpBase", lv_data(0), True)
    ad.add("atkBase", lv_data(1), True)
    ad.add("defBase", lv_data(2), True)
    grow_key = keys[3] if len(keys) > 3 else ""
    # JS: !/(hp|atk|def)/.test(keys[3]) → 突破属性为百分比类时不计白值
    ad.add(grow_key, lv_data(3, True), not re.search(r"(hp|atk|def)", grow_key))

    for key, val in (_CHAR_SELF_ATTRS.get(char.id) or {}).items():
        ad.add(key, val, True)

    # 角色静态 Buff（calc.js 中 isStatic 的 buff，如心海/行秋被动）
    for data in _char_static_buffs(char, game):
        for key, val in data.items():
            ad.add(key, val)


_CHAR_BUFFS_CACHE: dict[tuple[str, str], list[dict[str, float]]] = {}


def _char_static_buffs(char: meta.CharacterMeta, game: str) -> list[dict[str, float]]:
    """角色 calc.js 中 isStatic buff 的 data 列表（CharCfg.getCalcRule 的 buffs 部分）"""
    key = (game, char.name)
    if key in _CHAR_BUFFS_CACHE:
        return _CHAR_BUFFS_CACHE[key]
    ret: list[dict[str, float]] = []
    calc_file = meta._res_root() / f"meta-{game}" / "character" / char.name / "calc.js"
    if calc_file.is_file():
        try:
            exports = meta._eval_esm(calc_file, ["buffs"])
        except Exception:
            exports = {}
        for buff in exports.get("buffs") or []:
            if isinstance(buff, dict) and buff.get("isStatic") and buff.get("data"):
                ret.append(buff["data"])
    _CHAR_BUFFS_CACHE[key] = ret
    return ret


# ---------------------------------------------------------------------------
# 武器属性（Attr.setWeaponAttr + Weapon.calcAttr）
# ---------------------------------------------------------------------------


def _weapon_calc_attr_gs(w_meta: dict[str, Any], level: int, promote: int) -> dict[str, Any] | None:
    """移植 Weapon.calcAttr 原神分支：白值线性插值 + 副词条按 5 级阶梯"""
    attr = w_meta.get("attr") or {}
    if not attr:
        return None
    lv_step = [1, 20, 40, 50, 60, 70, 80, 90]
    lv_left, lv_right = 1, 20
    curr = 0
    for idx in range(len(lv_step) - 1):
        if promote == -1 or curr == promote:
            if lv_step[idx] <= level <= lv_step[idx + 1]:
                lv_left, lv_right = lv_step[idx], lv_step[idx + 1]
                break
        curr += 1
    w_atk = attr.get("atk") or {}
    v_left = w_atk.get(f"{lv_left}+") or w_atk.get(str(lv_left)) or 0
    v_right = w_atk.get(str(lv_right)) or 0
    atk_base = v_left + (v_right - v_left) * (level - lv_left) / (lv_right - lv_left)
    bonus = attr.get("bonusData") or {}
    value = None
    if attr.get("bonusKey") and bonus:
        b_left = bonus.get(f"{lv_left}+") or bonus.get(str(lv_left))
        b_right = bonus.get(str(lv_right))
        if b_left is not None and b_right is not None:
            step_count = math.ceil((lv_right - lv_left) / 5)
            value_step = (b_right - b_left) / step_count
            value = b_left + (step_count - math.ceil((lv_right - level) / 5)) * value_step
    return {"atkBase": atk_base, "attr": {"key": attr.get("bonusKey"), "value": value}}


def _weapon_calc_attr_sr(w_meta: dict[str, Any], level: int, promote: int) -> dict[str, float] | None:
    """移植 Weapon.calcAttr 星铁分支：attr[promote].attrs + growAttr*(level-1)"""
    meta_attr = w_meta.get("attr") or {}
    lv_attr = meta_attr.get(str(promote))
    if not lv_attr:
        return None
    ret: dict[str, float] = {}
    for k, v in (lv_attr.get("attrs") or {}).items():
        ret[k] = float(v)
    for k, v in (w_meta.get("growAttr") or {}).items():
        ret[k] = ret.get(k, 0.0) + float(v) * (level - 1)
    return ret


def _set_weapon_attr(ad: AttrData, avatar: dict[str, Any], char: meta.CharacterMeta, game: str) -> None:
    w_data = avatar.get("weapon") or {}
    if not w_data.get("name") and not w_data.get("id"):
        return
    w_meta = meta.get_weapon(str(w_data.get("name") or ""), game) or meta.get_weapon_by_id(w_data.get("id"), game)
    if not w_meta:
        return
    level = int(w_data.get("level") or 1)
    promote = int(w_data.get("promote") or 0)
    affix = int(w_data.get("affix") or 1)

    if game == "sr":
        ret = _weapon_calc_attr_sr(w_meta, level, promote)
        if ret:
            for k, v in ret.items():
                ad.add(k + ("Base" if k in ("hp", "atk", "def") else ""), v, True)
        # 光锥类型与角色命途一致时，精炼静态词条生效
        if w_meta.get("type") == char.get("weapon"):
            tables = (w_meta.get("skill") or {}).get("tables") or {}
            for data in _weapon_affix_buffs_sr(w_meta.get("type"), w_meta.get("name"), affix, tables):
                for k, v in data.items():
                    ad.add(k, v)
        return

    ret = _weapon_calc_attr_gs(w_meta, level, promote if promote else -1)
    if ret:
        ad.add("atkBase", ret["atkBase"])
        if ret["attr"].get("value") is not None:
            ad.add(ret["attr"].get("key"), ret["attr"]["value"])
    # 武器静态精炼词条（weapon/{type}/calc.js 中 isStatic 的 refine）
    for buff in _weapon_buffs_gs(w_meta.get("type")).get(w_meta.get("name")) or []:
        if not isinstance(buff, dict) or not buff.get("isStatic"):
            continue
        for key, r in (buff.get("refine") or {}).items():
            ad.add(key, r[affix - 1] * (buff.get("buffCount") or 1))


# gs 武器 calc.js 求值：export default function (step, staticStep) → {武器名: buff|[buffs]}
# JS 侧 stub 与 resources/meta-gs/weapon/index.js 一致
_GS_WEAPON_CALC_STUBS = """
var step = function (start, _step) {
  if (!_step) { _step = start / 4 }
  var ret = []
  for (var idx = 0; idx <= 5; idx++) { ret.push(start + _step * idx) }
  return ret
}
var staticStep = function (key, start, _step) {
  var refine = {}
  refine[key] = step(start, _step)
  return { title: key + '提高[key]', isStatic: true, refine: refine }
}
"""

_WEAPON_BUFFS_GS_CACHE: dict[str, dict[str, Any]] = {}


def _weapon_buffs_gs(w_type: str | None) -> dict[str, Any]:
    """原神某类型武器的精炼词条表（函数值经 JSON 序列化丢弃，仅保留静态数据）"""
    if not w_type:
        return {}
    if w_type in _WEAPON_BUFFS_GS_CACHE:
        return _WEAPON_BUFFS_GS_CACHE[w_type]
    ret: dict[str, Any] = {}
    calc_file = meta._res_root() / "meta-gs" / "weapon" / w_type / "calc.js"
    if calc_file.is_file():
        code = meta._strip_esm(calc_file.read_text(encoding="utf-8"))
        script = f"{_GS_WEAPON_CALC_STUBS}\n{code}\n;JSON.stringify(__default__(step, staticStep))"
        try:
            ret = json.loads(eval_js(script)) or {}
        except Exception:
            ret = {}
    # JS: isPlainObject 的 buff 会包成数组，这里统一成 list 方便消费
    for name, buffs in ret.items():
        if isinstance(buffs, dict):
            ret[name] = [buffs]
    _WEAPON_BUFFS_GS_CACHE[w_type] = ret
    return ret


# sr 武器 calc.js：export default function (staticIdx, keyIdx)
# stub 与 resources/meta-sr/weapon/index.js 一致；随后在 JS 内完成
# Weapon.getWeaponAffixBuffs(affix, isStatic=true) 的静态词条解析（含函数型 buff）
_SR_WEAPON_CALC_STUBS = """
var staticIdx = function (idx, key) {
  return { isStatic: true, idx: idx, key: key }
}
var keyIdx = function (title, key, idx) {
  if (key !== null && typeof key === 'object') {
    return function (tables) {
      var data = {}
      Object.keys(key).forEach(function (k) { data[k] = tables[key[k]] })
      return { title: title, data: data }
    }
  }
  return { title: title, idx: idx, key: key }
}
"""

_WEAPON_BUFFS_SR_CACHE: dict[tuple[str, str, int], list[dict[str, float]]] = {}
_WEAPON_SR_CODE_CACHE: dict[str, str] = {}


def _weapon_affix_buffs_sr(
    w_type: str | None, w_name: str | None, affix: int, tables: dict[str, Any]
) -> list[dict[str, float]]:
    """星铁光锥精炼静态词条：移植 Weapon.getWeaponAffixBuffs(affix, true) 的返回值（data 列表）"""
    if not w_type or not w_name:
        return []
    affix = max(1, int(affix or 1))
    cache_key = (w_type, w_name, affix)
    if cache_key in _WEAPON_BUFFS_SR_CACHE:
        return _WEAPON_BUFFS_SR_CACHE[cache_key]
    if w_type not in _WEAPON_SR_CODE_CACHE:
        calc_file = meta._res_root() / "meta-sr" / "weapon" / w_type / "calc.js"
        code = meta._strip_esm(calc_file.read_text(encoding="utf-8")) if calc_file.is_file() else ""
        _WEAPON_SR_CODE_CACHE[w_type] = code
    code = _WEAPON_SR_CODE_CACHE[w_type]
    ret: list[dict[str, float]] = []
    if code:
        script = f"""
{_SR_WEAPON_CALC_STUBS}
{code}
var __rawTables = {json.dumps(tables)}
var tables = {{}}
Object.keys(__rawTables).forEach(function (idx) {{ tables[idx] = __rawTables[idx][{affix - 1}] }})
var buffs = __default__(staticIdx, keyIdx)
var wBuffs = buffs[{json.dumps(w_name)}] || []
if (!Array.isArray(wBuffs)) {{ wBuffs = [wBuffs] }}
var ret = []
wBuffs.forEach(function (ds) {{
  if (typeof ds === 'function') {{ ds = ds(tables) }}
  if (!ds || !ds.isStatic) {{ return }}
  var tmp = {{}}
  if (ds.idx && ds.key) {{
    if (!tables[ds.idx]) {{ return }}
    tmp[ds.key] = tables[ds.idx]
  }}
  if (ds.refine) {{
    Object.keys(ds.refine).forEach(function (key) {{ tmp[key] = ds.refine[key][{affix - 1}] * (ds.buffCount || 1) }})
  }}
  if (Object.keys(tmp).length) {{ ret.push(tmp) }}
}})
JSON.stringify(ret)
"""
        try:
            ret = json.loads(eval_js(script)) or []
        except Exception:
            ret = []
    _WEAPON_BUFFS_SR_CACHE[cache_key] = ret
    return ret


# ---------------------------------------------------------------------------
# 圣遗物词条（ArtisAttr.js）与套装（ArtifactSet.getArtisSetBuff）
# ---------------------------------------------------------------------------

# gs 圣遗物 id 第 4 位 → 部位（对齐 Artifact.get 的 [4, 2, 5, 1, 3][name[3] - 1]）
_GS_ID_POS = [4, 2, 5, 1, 3]

_GS_ARTI_INDEX: dict[str, tuple[str, dict[int, str]]] | None = None
_SR_ARTI_INDEX: dict[str, dict[str, Any]] | None = None


def _gs_arti_index() -> dict[str, tuple[str, dict[int, str]]]:
    """{id 前两位: (套装名, {部位: 散件名})}，由 artifact/data.json 构建"""
    global _GS_ARTI_INDEX
    if _GS_ARTI_INDEX is None:
        index: dict[str, tuple[str, dict[int, str]]] = {}
        for s in meta.get_artifact_meta("gs").values():
            pieces: dict[int, str] = {}
            prefix = ""
            for idx, piece in (s.get("idxs") or {}).items():
                pid = str(piece.get("id") or "")
                prefix = prefix or pid[:2]
                pieces[int(idx)] = piece.get("name") or ""
            if prefix:
                index[prefix] = (s.get("name") or "", pieces)
        _GS_ARTI_INDEX = index
    return _GS_ARTI_INDEX


def _sr_arti_index() -> dict[str, dict[str, Any]]:
    """{tid: {set, name, star, idx}}，由 artifact/data.json 的 idxs[].ids 构建"""
    global _SR_ARTI_INDEX
    if _SR_ARTI_INDEX is None:
        index: dict[str, dict[str, Any]] = {}
        for s in meta.get_artifact_meta("sr").values():
            for idx, piece in (s.get("idxs") or {}).items():
                for tid, star in (piece.get("ids") or {}).items():
                    index[str(tid)] = {
                        "set": s.get("name") or "",
                        "name": piece.get("name") or "",
                        "star": int(star),
                        "idx": int(idx),
                    }
        _SR_ARTI_INDEX = index
    return _SR_ARTI_INDEX


def resolve_artis_attr(piece: dict[str, Any], idx: int, game: str) -> dict[str, Any] | None:
    """移植 ArtisAttr.getData：mainId/attrIds → {main: {id,key,value}, attrs: [...]}

    gs attrIds 为 appendPropIdList（查 attrIdMap 累加）；sr attrIds 为 "affixId,cnt,step"。
    """
    main_id = piece.get("mainId")
    attr_ids = piece.get("attrIds") or []
    level = int(piece.get("level") or 0)
    star = int(piece.get("star") or 5)

    if game == "gs":
        extra = meta.artifact_extra("gs")
        attr_map = extra.get("attrMap") or {}
        key = (extra.get("mainIdMap") or {}).get(str(main_id))
        if not key:
            return None
        attr_cfg = attr_map["dmg" if is_elem(key, "gs") else key]
        pos_eff = 2 if key in ("hpPlus", "atkPlus", "defPlus") else 1
        star_eff = {1: 0.21, 2: 0.36, 3: 0.6, 4: 0.9, 5: 1}
        main = {
            "id": main_id,
            "key": key,
            "value": attr_cfg["value"] * (1.2 + 0.34 * level) * pos_eff * star_eff[star or 5],
        }
        attrs: list[dict[str, Any]] = []
        tmp: dict[str, dict[str, Any]] = {}
        for aid in attr_ids:
            cfg = (extra.get("attrIdMap") or {}).get(str(aid))
            if not cfg:
                continue
            k, v = cfg["key"], float(cfg["value"])
            if k not in tmp:
                tmp[k] = {"key": k, "upNum": 0, "eff": 0.0, "value": 0.0}
                attrs.append(tmp[k])
            pct = attr_map[k].get("format") == "pct"
            tmp[k]["value"] += v * (100 if pct else 1)
            tmp[k]["upNum"] += 1
            tmp[k]["eff"] += v / attr_map[k]["value"] * (100 if pct else 1)
        return {"main": main, "attrs": attrs}

    # 星铁：mainIdx/starData 查表（artifact/meta.json）
    md = meta.artifact_star_meta("sr")
    main_key = ((md.get("mainIdx") or {}).get(str(idx)) or {}).get(str(main_id))
    star_cfg = (md.get("starData") or {}).get(str(star)) or {}
    main_cfg = (star_cfg.get("main") or {}).get(main_key or "")
    if not main_id or not main_cfg:
        return None
    main = {"id": main_id, "key": main_key, "value": main_cfg["base"] + main_cfg["step"] * level}
    attrs = []
    for ds in attr_ids:
        if isinstance(ds, str):
            parts = ds.split(",")
            ds = {"id": parts[0], "count": parts[1] if len(parts) > 1 else 1, "step": parts[2] if len(parts) > 2 else 0}
        attr_cfg = (star_cfg.get("sub") or {}).get(str(ds.get("id")))
        if not attr_cfg:
            continue
        count = int(ds.get("count") or 0)
        step = int(ds.get("step") or 0)
        value = attr_cfg["base"] * count + attr_cfg["step"] * step
        attrs.append({
            **ds,
            "key": attr_cfg.get("key"),
            "upNum": count,
            "eff": value / (attr_cfg["base"] + attr_cfg["step"] * 2),
            "value": value,
        })
    return {"main": main, "attrs": attrs}


def resolve_artis(avatar: dict[str, Any], game: str) -> dict[int, dict[str, Any]]:
    """把 avatar['artis'] 解析成 {部位(int): {name, set, level, star, main, attrs}}

    对应 JS 的 Artis.setArtisData + setArtis：散件名/套装由 id 查 meta，
    词条由 resolve_artis_attr 换算；id 无法识别的部位跳过（对齐 Artifact.get 失败）。
    """
    ret: dict[int, dict[str, Any]] = {}
    for idx_str, ds in (avatar.get("artis") or {}).items():
        idx = int(idx_str)
        if game == "gs":
            id_str = str(ds.get("id") or "")
            if len(id_str) != 5 or not id_str.isdigit():
                continue
            hit = _gs_arti_index().get(id_str[:2])
            if not hit:
                continue
            set_name, pieces = hit
            pos = _GS_ID_POS[int(id_str[3]) - 1]
            piece = {
                "id": ds.get("id"),
                "name": pieces.get(pos) or "",
                "set": set_name,
                "level": int(ds.get("level") or 0),
                "star": int(ds.get("star") or 5),
            }
        else:
            hit = _sr_arti_index().get(str(ds.get("id") or ""))
            if not hit:
                continue
            piece = {
                "id": ds.get("id"),
                "name": hit["name"],
                "set": hit["set"],
                "level": int(ds.get("level") or 0),
                "star": hit["star"],
            }
        if ds.get("mainId") and ds.get("attrIds") is not None:
            attr = resolve_artis_attr({**ds, "star": piece["star"]}, idx, game)
            if attr:
                piece["main"] = attr["main"]
                piece["attrs"] = attr["attrs"]
        piece.setdefault("main", None)
        piece.setdefault("attrs", [])
        ret[idx] = piece
    return ret


def artis_set_data(pieces: dict[int, dict[str, Any]], game: str) -> dict[str, Any]:
    """移植 ArtisSet.getSetData：{sets: {套装名: 2|4}, names, abbrs}（仅统计 >=2 件的套装）"""
    set_count: dict[str, int] = {}
    for piece in pieces.values():
        if piece.get("set"):
            set_count[piece["set"]] = set_count.get(piece["set"], 0) + 1
    abbr_map = _set_abbr(game)
    sets: dict[str, int] = {}
    names: list[str] = []
    abbrs: list[str] = []
    for set_name, count in set_count.items():
        if count >= 2:
            num = 4 if count >= 4 else 2
            sets[set_name] = num
            names.append(set_name)
            abbrs.append(f"{abbr_map.get(set_name, set_name)}{num}")
            abbrs.append(f"{set_name}{num}")
    return {"sets": sets, "names": names, "abbrs": abbrs}


_SET_ABBR_CACHE: dict[str, dict[str, str]] = {}


def _set_abbr(game: str) -> dict[str, str]:
    """套装简称表：gs alias.js 的 setAbbr / sr 的 artiSetAbbr"""
    if game in _SET_ABBR_CACHE:
        return _SET_ABBR_CACHE[game]
    alias_file = meta._res_root() / f"meta-{game}" / "artifact" / "alias.js"
    ret: dict[str, str] = {}
    if alias_file.is_file():
        try:
            exports = meta._eval_esm(alias_file, ["setAbbr", "artiSetAbbr"])
            ret = exports.get("setAbbr") or exports.get("artiSetAbbr") or {}
        except Exception:
            ret = {}
    _SET_ABBR_CACHE[game] = ret
    return ret


_ARTI_BUFFS_CACHE: dict[str, dict[str, Any]] = {}


def _arti_buffs(game: str) -> dict[str, Any]:
    """套装效果表（artifact/calc.js 的 default 导出）"""
    if game in _ARTI_BUFFS_CACHE:
        return _ARTI_BUFFS_CACHE[game]
    calc_file = meta._res_root() / f"meta-{game}" / "artifact" / "calc.js"
    ret: dict[str, Any] = {}
    if calc_file.is_file():
        try:
            ret = meta._eval_esm(calc_file, ["__default__"]).get("__default__") or {}
        except Exception:
            ret = {}
    _ARTI_BUFFS_CACHE[game] = ret
    return ret


def _get_artis_set_buff(name: str, num: int, game: str) -> list[dict[str, Any]]:
    """移植 ArtifactSet.getArtisSetBuff"""
    buffs = _arti_buffs(game)
    ret = (buffs.get(name) or {}).get(str(num)) or buffs.get(name + str(num))
    if not ret:
        return []
    return [ret] if isinstance(ret, dict) else ret


def _calc_artis_attr(ad: AttrData, ds: dict[str, Any] | None, char_elem: str, game: str) -> None:
    """移植 Attr.calcArtisAttr：单条圣遗物词条加入面板

    注意 JS 里 Format.isElem(key) 未传 game（默认 gs），星铁的元素伤词条（ice/fire 等）
    不在 gs 元素表内、走到 AttrData.addAttr 的 sr 分支转换，结果一致，这里照此实现。
    """
    if not ds:
        return
    key = ds.get("key")
    if is_elem(key, "gs"):
        if char_elem == key:
            key = "dmg"
        elif key in ("electro", "pyro", "hydro", "cryo"):
            key = "coloringDmg"
    if not key:
        return
    if key in ("atk", "hp", "def"):
        key += "Pct"
    ad.add(key, ds.get("value") or 0)


def _set_artis_attr(ad: AttrData, avatar: dict[str, Any], pieces: dict[int, dict[str, Any]], game: str) -> None:
    """移植 Attr.setArtisAttr：圣遗物主副词条 + 套装静态词条"""
    char_elem = avatar.get("elem") or ""
    for piece in pieces.values():
        _calc_artis_attr(ad, piece.get("main"), char_elem, game)
        for ds in piece.get("attrs") or []:
            _calc_artis_attr(ad, ds, char_elem, game)
    # 套装静态加成（ArtifactSet.eachSet：>=4 件时先生效 2 件套再生效 4 件套）
    sets = artis_set_data(pieces, game)["sets"]
    for set_name, num in sets.items():
        for n in ([2, num] if num >= 4 else [num]):
            for buff in _get_artis_set_buff(set_name, n, game):
                if not isinstance(buff, dict) or not buff.get("isStatic"):
                    continue
                if buff.get("elem") and not same_elem(char_elem, buff["elem"], game):
                    continue
                for key, val in (buff.get("data") or {}).items():
                    ad.add(key, val)


# ---------------------------------------------------------------------------
# 主入口（Attr.calc）
# ---------------------------------------------------------------------------


def calc_attr(avatar: dict[str, Any], game: str) -> dict[str, Any]:
    """计算角色面板属性，返回对齐 JS AttrData.getAttr() 的 dict

    avatar 为 datasource 解析结果（core/player.py 的 avatar 结构）。
    另附 "_base"（JS 的 Attr.getBase()）：白值构成统计。
    """
    meta._check_game(game)
    char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
    if not char:
        raise ValueError(f"未找到角色 meta: {avatar.get('name') or avatar.get('id')}")
    elem = avatar.get("elem") or char.get("elem") or ""
    ad = AttrData(game, char_elem=elem)
    if game == "gs":
        ad.add("recharge", 100, True)
        ad.add("cpct", 5, True)
        ad.add("cdmg", 50, True)
    _set_char_attr(ad, avatar, char, game)
    _set_weapon_attr(ad, avatar, char, game)
    pieces = resolve_artis(avatar, game)
    _set_artis_attr(ad, avatar, pieces, game)
    ret = ad.get_attr()
    ret["_base"] = ad.get_base()
    return ret
