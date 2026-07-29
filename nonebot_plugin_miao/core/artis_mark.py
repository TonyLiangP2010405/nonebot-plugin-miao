"""圣遗物评分：移植 refs/miao-plugin 的
- models/artis/ArtisMarkCfg.js（角色评分权重：artis.js 规则 → usefulAttr → 默认权重，
  含武器/绝缘4/西风修正）+ getCfg（attrMap → attrs{weight,fixWeight,mark} + posMaxMark）
- models/artis/ArtisMark.js（getMark 单件评分、getMaxMark 部位理论最高、
  getMarkClass 档位、getMarkDetail 汇总）

角色自定义规则（resources/meta-{game}/character/{name}/artis.js）用 quickjs 执行
`export default function ({attr, rule, def})`：rule(title, weight) 直接返回规则结果，
def(weight) 的权重修正（武器/套装/西风）在 Python 侧按 ArtisMarkCfg.js 的 def() 实现。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from . import meta
from .attr_calc import artis_set_data, elem_name, is_elem, resolve_artis, same_elem
from .jseval import eval_js

# ArtisMarkCfg.js weaponCfg：特定武器携带时提高对应属性权重
_WEAPON_CFG = {
    "磐岩结绿": {"attr": "hp", "abbr": "绿剑", "max": 30, "min": 15},
    "猎人之径": {"attr": "mastery"},
    "薙草之稻光": {"attr": "recharge", "abbr": "薙刀"},
    "护摩之杖": {"attr": "hp", "abbr": "护摩", "max": 18, "min": 10},
}

# 无 artis.js 且不在 usefulAttr 时的默认权重（ArtisMarkCfg.js 的 defaultAttrWeight）
_DEFAULT_WEIGHT = {
    "gs": {"atk": 75, "cpct": 100, "cdmg": 100, "dmg": 100, "phy": 100},
    "sr": {"atk": 75, "cpct": 100, "cdmg": 100, "dmg": 100, "speed": 100},
}

# ArtisMark.getMarkClass 的档位表
_SCORE_MAP = [("D", 7), ("C", 14), ("B", 21), ("A", 28), ("S", 35), ("SS", 42), ("SSS", 49), ("ACE", 56), ("MAX", 70)]

# 法尔伽：所有异色属伤杯在评分时视为风伤杯（ArtisMark.getMark）
_FARGUS_ID = 10000128


def _js_round(x: float) -> int:
    """对齐 JS Math.round（.5 向正无穷取整）"""
    return math.floor(x + 0.5)


def get_mark_class(mark: float) -> str | None:
    """移植 ArtisMark.getMarkClass（mark >= 70 时 JS 返回 undefined，这里返回 None）"""
    for name, threshold in _SCORE_MAP:
        if mark < threshold:
            return name
    return None


# ---------------------------------------------------------------------------
# usefulAttr 通用权重表（artifact/artis-mark.js）
# ---------------------------------------------------------------------------

_USEFUL_ATTR_CACHE: dict[str, dict[str, Any]] = {}


def _useful_attr(game: str) -> dict[str, Any]:
    if game in _USEFUL_ATTR_CACHE:
        return _USEFUL_ATTR_CACHE[game]
    mark_file = meta._res_root() / f"meta-{game}" / "artifact" / "artis-mark.js"
    ret: dict[str, Any] = {}
    if mark_file.is_file():
        try:
            ret = meta._eval_esm(mark_file, ["usefulAttr"]).get("usefulAttr") or {}
        except Exception:
            ret = {}
    _USEFUL_ATTR_CACHE[game] = ret
    return ret


# ---------------------------------------------------------------------------
# 角色评分权重（ArtisMarkCfg.getCharArtisCfg）
# ---------------------------------------------------------------------------


def _apply_def(
    weight_arg: dict[str, Any] | None,
    char: meta.CharacterMeta,
    weapon: dict[str, Any],
    abbrs: list[str],
    game: str,
) -> dict[str, Any]:
    """移植 ArtisMarkCfg.js 的 def()：合并权重 + 武器/绝缘4/西风修正 + 标题拼接"""
    weight = dict(weight_arg or _useful_attr(game).get(char.name) or {})
    weapon_name = weapon.get("name") or ""
    weapon_affix = int(weapon.get("affix") or 0)
    title: list[str] = []

    def check(key: str, max_: float = 75, max_plus: float = 75, is_weapon: bool = True) -> bool:
        original = weight.get(key) or 0
        if original < max_:
            plus = max_plus * (1 + weapon_affix / 5) / 2 if is_weapon else max_plus
            weight[key] = min(_js_round(original + plus), max_)
            return True
        return False

    def weapon_check(key: str, max_affix_attr: float = 20, min_affix_attr: float = 10, max_: float = 100) -> bool:
        original = weight.get(key) or 0
        if original == max_:
            return False
        plus = min_affix_attr + (max_affix_attr - min_affix_attr) * (weapon_affix - 1) / 4
        weight[key] = min(_js_round(original + plus), max_)
        return True

    if game == "gs":
        # 增加攻击力或直接伤害类武器判定
        w_cfg = _WEAPON_CFG.get(weapon_name)
        if (weight.get("atk") or 0) > 0 and w_cfg:
            if weapon_check(w_cfg["attr"], w_cfg.get("max", 20), w_cfg.get("min", 10)):
                title.append(w_cfg.get("abbr") or weapon_name)

        # 绝缘4：充能权重拉高至沙漏圣遗物当前最高权重齐平
        max_weight = max(weight.get(k) or 0 for k in ("atk", "hp", "def", "mastery"))
        if "绝缘4" in abbrs and check("recharge", max_weight, 75, False):
            title.append("绝缘4")

        # 西风系列武器：暴击权重强制提高至 100
        if re.match(r"^西风(长枪|大剑|剑|猎弓|秘典)$", weapon_name) and (weight.get("cpct") or 0) < 100:
            weight["cpct"] = 100
            title.append("西风")

    title_str = "".join(title) if title else "通用"
    return {"title": f"{char.get('abbr') or char.name}-{title_str}", "attrWeight": weight}


def get_char_weight(
    name: str,
    attr: dict[str, Any],
    game: str,
    weapon: dict[str, Any] | None = None,
    abbrs: list[str] | None = None,
    cons: int = 0,
    elem: str = "",
) -> dict[str, Any]:
    """角色评分权重：{title, attrWeight}（移植 ArtisMarkCfg.getCharArtisCfg）

    优先执行角色 artis.js 自定义规则；不存在时用 usefulAttr → 默认权重走 def() 修正。
    weapon/abbrs/cons/elem 供 def() 修正与自定义规则使用。
    """
    char = meta.get_character(name, game)
    if not char:
        raise ValueError(f"未找到角色 meta: {name}")
    weapon = weapon or {}
    abbrs = abbrs or []

    # gs 旅行者评分规则在「旅行者」目录下（CharCfg.getArtisCfg）
    char_dir = "旅行者" if game == "gs" and char.name == "旅行者" else char.name
    rule_file = meta._res_root() / f"meta-{game}" / "character" / char_dir / "artis.js"
    if rule_file.is_file():
        attr_args = {k: v for k, v in attr.items() if not str(k).startswith("_") and k != "staticAttr"}
        code = meta._strip_esm(rule_file.read_text(encoding="utf-8"))
        script = f"""
{code}
var rule = function (title, attrWeight) {{ return {{ title: title, attrWeight: attrWeight, kind: 'rule' }} }}
var def = function (attrWeight) {{ return {{ attrWeight: attrWeight || null, kind: 'def' }} }}
var __abbrs = {json.dumps(abbrs, ensure_ascii=False)}
var artis = {{ is: function (check) {{
  var ret = false
  String(check).split(',').forEach(function (s) {{ if (__abbrs.indexOf(s) >= 0) {{ ret = true }} }})
  return ret
}} }}
var __ret = __default__({{
  attr: {json.dumps(attr_args, ensure_ascii=False)},
  elem: {json.dumps(elem, ensure_ascii=False)},
  artis: artis,
  rule: rule,
  def: def,
  weapon: {json.dumps({"name": weapon.get("name") or "", "affix": int(weapon.get("affix") or 0)}, ensure_ascii=False)},
  cons: {int(cons or 0)}
}})
JSON.stringify(__ret)
"""
        result = json.loads(eval_js(script))
        if result and result.get("kind") == "rule":
            return {"title": result["title"], "attrWeight": result["attrWeight"] or {}}
        return _apply_def((result or {}).get("attrWeight"), char, weapon, abbrs, game)

    # 无自定义规则：def(usefulAttr[char.name] || defaultAttrWeight)
    default = _useful_attr(game).get(char.name) or _DEFAULT_WEIGHT[game]
    return _apply_def(default, char, weapon, abbrs, game)


# ---------------------------------------------------------------------------
# getCfg：attrMap → attrs + posMaxMark
# ---------------------------------------------------------------------------


def _get_max_attr(attrs: dict[str, Any], lst: list[str], max_len: int = 1, ban_attr: str = "") -> list[str]:
    """移植 ArtisMark.getMaxAttr（lodash.sortBy 升序稳定排序后 reverse）"""
    tmp = [(a, attrs[a]["fixWeight"]) for a in lst if a != ban_attr and a in attrs]
    tmp = sorted(tmp, key=lambda t: t[1])  # 稳定升序
    tmp.reverse()
    return [a for a, _ in tmp[:max_len]]


def _get_max_mark(attrs: dict[str, Any], game: str) -> dict[Any, float]:
    """移植 ArtisMark.getMaxMark：各部位理论最高分（含 'm{idx}' 主词条分）"""
    extra = meta.artifact_extra(game)
    main_attr = extra.get("mainAttr") or {}
    sub_attr = extra.get("subAttr") or []
    ret: dict[Any, float] = {}
    for idx in range(1, 6 if game == "gs" else 7):
        total_mark = 0.0
        m_mark = 0.0
        if idx == 1:
            m_attr = "hpPlus"
        elif idx == 2:
            m_attr = "atkPlus"
        else:
            candidates = _get_max_attr(attrs, main_attr.get(str(idx)) or [])
            if candidates:
                m_attr = candidates[0]
                m_mark = attrs[m_attr]["fixWeight"]
                total_mark += m_mark * 2
            else:
                m_attr = (main_attr.get(str(idx)) or [""])[0]
        s_attr = _get_max_attr(attrs, sub_attr, 4, m_attr)
        for a_idx, a in enumerate(s_attr):
            total_mark += attrs[a]["fixWeight"] * (6 if a_idx == 0 else 1)
        ret[idx] = total_mark
        ret[f"m{idx}"] = m_mark
    return ret


def _get_cfg(char: meta.CharacterMeta, attr_weight: dict[str, Any], title: str, game: str) -> dict[str, Any]:
    """移植 ArtisMarkCfg.getCfg：{attrs, classTitle, posMaxMark}"""
    attr_map = meta.artifact_extra(game).get("attrMap") or {}
    base_attr = char.get("baseAttr") or {"hp": 14000, "atk": 230, "def": 700}
    attrs: dict[str, Any] = {}
    for key, attr in attr_map.items():
        k = attr.get("base") or ""
        weight = attr_weight.get(k or key)
        if not weight or float(weight) == 0:
            continue
        ret = dict(attr)
        ret["weight"] = weight
        ret["fixWeight"] = weight
        ret["mark"] = weight / attr["value"]
        if k:
            plus = 520 if k == "atk" else 0
            ret["mark"] = weight / attr_map[k]["value"] / (base_attr[k] + plus) * 100
            ret["fixWeight"] = weight * attr["value"] / attr_map[k]["value"] / (base_attr[k] + plus) * 100
        attrs[key] = ret
    return {
        "attrs": attrs,
        "classTitle": title,
        "posMaxMark": _get_max_mark(attrs, game),
    }


# ---------------------------------------------------------------------------
# getMark：单件评分
# ---------------------------------------------------------------------------


def _get_mark(
    char_cfg: dict[str, Any], idx: int, arti: dict[str, Any], elem: str, game: str, char_id: Any
) -> tuple[float, str | None]:
    """移植 ArtisMark.getMark，返回 (mark, 主词条计分用 key)"""
    attrs = char_cfg["attrs"]
    pos_max_mark = char_cfg["posMaxMark"]
    m_attr = arti.get("main") or {}
    key = m_attr.get("key")
    if not key:
        return 0.0, None
    ret = 0.0
    fix_pct = 1.0
    main_key = key
    if idx >= 3:
        if key != "recharge":
            dmg_idx = 4 if game == "gs" else 5
            if idx == dmg_idx:
                # 法尔伽特殊处理：异色属伤杯均视为风伤杯
                if same_elem(elem, key, game) or char_id == _FARGUS_ID:
                    main_key = "dmg"
            m_max = pos_max_mark.get(f"m{idx}", 0)
            main_weight = (attrs.get(main_key) or {}).get("weight") or 0
            fix_pct = max(0.0, min(1.0, main_weight / m_max)) if m_max > 0 else 1
            if game == "gs" and main_key in ("atk", "hp", "def") and main_weight >= 75:
                fix_pct = 1
        ret += ((attrs.get(main_key) or {}).get("mark") or 0) * (m_attr.get("value") or 0) / 4

    for ds in arti.get("attrs") or []:
        ret += ((attrs.get(ds.get("key")) or {}).get("mark") or 0) * (ds.get("value") or 0)
    p_max = pos_max_mark.get(idx, 0)
    return (ret * (1 + fix_pct) / 2 / p_max * 66 if p_max > 0 else 0.0), main_key


# ---------------------------------------------------------------------------
# 主入口（ArtisMark.getMarkDetail）
# ---------------------------------------------------------------------------


def calc_mark(avatar: dict[str, Any], attr: dict[str, Any], game: str) -> dict[str, Any]:
    """计算圣遗物评分，返回：
    {
        classTitle,                    # 权重标题（如 '优菈-通用'）
        artis: {idx(int): {name, set, level, main, attrs, mark, markClass, mainKey, mainWeight}},
        mark,                          # 总分（未修约）
        markClass,                     # 总档位（按 总分/件数）
        sets, names,                   # 套装统计
        charWeight,                    # {词条key: weight}
    }
    """
    meta._check_game(game)
    char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
    if not char:
        raise ValueError(f"未找到角色 meta: {avatar.get('name') or avatar.get('id')}")
    elem = avatar.get("elem") or char.get("elem") or ""
    pieces = resolve_artis(avatar, game)
    set_data = artis_set_data(pieces, game)
    weapon = avatar.get("weapon") or {}
    cw = get_char_weight(
        char.name, attr, game,
        weapon=weapon, abbrs=set_data["abbrs"], cons=int(avatar.get("cons") or 0), elem=elem,
    )
    char_cfg = _get_cfg(char, cw["attrWeight"], cw["title"], game)

    artis_ret: dict[int, Any] = {}
    total_mark = 0.0
    for idx in sorted(pieces):
        arti = pieces[idx]
        mark, main_key = _get_mark(char_cfg, idx, arti, elem, game, avatar.get("id"))
        total_mark += mark
        artis_ret[idx] = {
            "name": arti.get("name") or "",
            "set": arti.get("set") or "",
            "level": arti.get("level") or 0,
            "main": arti.get("main"),
            "attrs": arti.get("attrs") or [],
            "mark": mark,
            "markClass": get_mark_class(mark),
            "mainKey": main_key,
            "mainWeight": (char_cfg["attrs"].get(main_key) or {}).get("weight") if main_key else None,
        }
    return {
        "classTitle": char_cfg["classTitle"],
        "artis": artis_ret,
        "mark": total_mark,
        "markClass": get_mark_class(total_mark / (5 if game == "gs" else 6)),
        "sets": set_data["sets"],
        "names": set_data["names"],
        "charWeight": {key: ds["weight"] for key, ds in char_cfg["attrs"].items()},
    }


# ---------------------------------------------------------------------------
# 展示辅助（ArtisMark.getKeyTitleMap / formatArti 的格式化部分）
# ---------------------------------------------------------------------------


def key_title(key: str | None, game: str) -> str:
    """词条 key → 中文标题（attrMap.title；元素 key 为 '{元素}伤加成'）"""
    if not key:
        return ""
    attr_map = meta.artifact_extra(game).get("attrMap") or {}
    if key in attr_map:
        return attr_map[key].get("title") or key
    if is_elem(key, game):
        return f"{elem_name(key, game)}伤加成"
    return key


def format_value(key: str | None, value: float, game: str) -> str:
    """词条数值展示：pct 词条保留 1 位小数加 %，comma 词条取整"""
    attr_map = meta.artifact_extra(game).get("attrMap") or {}
    fmt = (attr_map.get("dmg" if is_elem(key, game) else (key or "")) or {}).get("format")
    if fmt == "pct":
        return f"{value:.1f}%"
    return str(round(value))
