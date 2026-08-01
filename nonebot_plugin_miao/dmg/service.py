"""Damage-calculation orchestration for upstream miao ``calc.js`` rules.

改编 refs/miao-plugin/models/ProfileDmg.js（calcData，mode 'dmg'）：
Python 侧负责把 avatar 解析成原版 ProfileDmg 的输入（面板 attr、天赋表、
武器精炼表、套装表、attrMap），角色/武器/圣遗物规则与伤害引擎全部在
QuickJS 沙箱内以求值真源码的方式运行（见 dmg/js/ 与 dmg/js_runtime.py）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core import meta
from ..core.attr_calc import artis_set_data, calc_attr, elem_name, resolve_artis
from ..core.jseval import eval_js
from .js_runtime import RUNTIME


class DamageError(ValueError):
    """A user-safe damage calculation error."""


# ---------------------------------------------------------------------------
# 天赋数据（移植 ProfileDmg.talent()）
# ---------------------------------------------------------------------------

# 星铁遍历的天赋 key（ProfileDmg.js L50）；原神仅 a,e,q
_SR_TALENT_KEYS = "a,a2,e,e1,e2,q,q2,q3,t,t2,xe,xe2,me,me2,mt,mt1,mt2".split(",")
# e2/q3 等后缀 key 的等级回退到基础 key（ProfileDmg.js L52-56）
_TALENT_KEY_RET = re.compile(r"^(a|e|q|t|xe|me|mt)(1|2|3)$")


def _talent_level(levels: dict[str, Any], key: str) -> int:
    def level_of(k: str) -> int:
        v = levels.get(k)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, dict):
            return int(v.get("level") or 1)
        return 1

    m = _TALENT_KEY_RET.match(key)
    return level_of(m.group(1)) if m else level_of(key)


def _talent(avatar: dict[str, Any], char: meta.CharacterMeta, game: str) -> dict[str, dict[str, Any]]:
    levels = avatar.get("talent") or {}
    keys = _SR_TALENT_KEYS if game == "sr" else ["a", "e", "q"]
    talent_data = char.get("talentData") or {}
    talent_meta = char.get("talent") or {}
    ret: dict[str, dict[str, Any]] = {}
    for key in keys:
        level = _talent_level(levels, key)
        row: dict[str, Any] = {}
        if game == "gs" and talent_data:
            # 原神用 talentData（数值/数组原样），不用带 % 的展示表 talent.tables
            for name, ds in (talent_data.get(key) or {}).items():
                if isinstance(ds, list) and 0 <= level - 1 < len(ds):
                    row[name] = ds[level - 1]
        elif game == "sr" and talent_meta.get(key):
            tables = talent_meta[key].get("tables") or {}
            rows = tables.values() if isinstance(tables, dict) else tables
            for ds in rows:
                values = ds.get("values") or []
                if 0 <= level - 1 < len(values):
                    row[str(ds.get("name") or "")] = values[level - 1]
        ret[key] = row
    return ret


# ---------------------------------------------------------------------------
# 规则文件与沙箱脚本组装
# ---------------------------------------------------------------------------


def _rule_file(char: meta.CharacterMeta, avatar: dict[str, Any], game: str):
    name = char.name
    if game == "gs" and name == "旅行者":
        name = f"旅行者/{avatar.get('elem') or char.get('elem') or 'anemo'}"
    path = meta._res_root() / f"meta-{game}" / "character" / name / "calc.js"
    return path if path.is_file() else None


# gs 武器 calc.js 求值 stub（与 resources/meta-gs/weapon/index.js 一致）
_GS_WEAPON_STUBS = """
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

# sr 武器 calc.js 求值 stub（与 resources/meta-sr/weapon/index.js 一致）
_SR_WEAPON_STUBS = """
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

# 沙箱内收集角色 calc.js 导出的包装（typeof 守卫跳过缺失导出）
_CHAR_RULE_WRAP = """\
var __charRule = (function () {{
{code}
return {{
  buffs: (typeof buffs === 'undefined' ? undefined : buffs),
  details: (typeof details === 'undefined' ? undefined : details),
  defParams: (typeof defParams === 'undefined' ? undefined : defParams),
  defDmgIdx: (typeof defDmgIdx === 'undefined' ? undefined : defDmgIdx),
  defDmgKey: (typeof defDmgKey === 'undefined' ? undefined : defDmgKey),
  mainAttr: (typeof mainAttr === 'undefined' ? undefined : mainAttr),
  enemyName: (typeof enemyName === 'undefined' ? undefined : enemyName)
}}
}})()"""


def _weapon_rule_code(game: str, w_type: str | None) -> str:
    """武器类型 calc.js 的求值脚本：__weaponBuffs = {武器名: buff|[buffs]}"""
    if not w_type:
        return "var __weaponBuffs = {}"
    calc_file = meta._res_root() / f"meta-{game}" / "weapon" / w_type / "calc.js"
    if not calc_file.is_file():
        return "var __weaponBuffs = {}"
    code = meta._strip_esm(calc_file.read_text(encoding="utf-8"))
    if game == "gs":
        stubs, args = _GS_WEAPON_STUBS, "step, staticStep"
    else:
        stubs, args = _SR_WEAPON_STUBS, "staticIdx, keyIdx"
    return f"var __weaponBuffs = (function () {{\n{stubs}\n{code}\n;return __default__({args}) }})()"


def _arti_rule_code(game: str) -> str:
    """圣遗物套装 calc.js 的求值脚本：__artiBuffs = default 导出"""
    calc_file = meta._res_root() / f"meta-{game}" / "artifact" / "calc.js"
    if not calc_file.is_file():
        return "var __artiBuffs = {}"
    code = meta._strip_esm(calc_file.read_text(encoding="utf-8"))
    return f"var __artiBuffs = (function () {{\n{code}\n;return __default__ }})()"


def _build_script(game: str, w_type: str | None, rule_code: str, payload: dict[str, Any]) -> str:
    attr_map = (meta.artifact_extra(game).get("attrMap") or {}) if game in ("gs", "sr") else {}
    return "\n".join(
        [
            RUNTIME,
            _weapon_rule_code(game, w_type),
            _arti_rule_code(game),
            f"var __attrMap = {json.dumps(attr_map, ensure_ascii=False)}",
            f"var __weaponTables = {json.dumps(payload.get('weaponTables') or {}, ensure_ascii=False)}",
            _CHAR_RULE_WRAP.format(code=rule_code),
            f"__miao_run({json.dumps(payload, ensure_ascii=False)})",
        ]
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def calc_dmg(avatar: dict[str, Any], game: str, idx: int | None = None) -> dict[str, Any]:
    """Evaluate an upstream character rule and return its damage rows.

    ``idx`` is one-based, matching ``/角色伤害N``.  Rules, callbacks and
    conditional rows run in QuickJS; Python supplies the validated panel and
    talent data only.
    """
    char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
    if not char:
        raise DamageError("未找到角色元数据，无法计算伤害")
    rule = _rule_file(char, avatar, game)
    if not rule:
        raise DamageError(f"{char.name} 暂无伤害计算规则")

    attr = calc_attr(avatar, game)
    weapon = avatar.get("weapon") or {}
    w_meta = meta.get_weapon(str(weapon.get("name") or ""), game) or {}
    # 原版 elemName 只查 gs 元素映射（sr 元素靠中文别名恰好命中）
    element = elem_name(avatar.get("elem") or char.get("elem") or "", "gs")
    pieces = resolve_artis(avatar, game)
    payload = {
        "game": game,
        "level": int(avatar.get("level") or 90),
        "cons": int(avatar.get("cons") or 0),
        "sp": char.get("sp") if game == "sr" else None,
        "uid": avatar.get("uid") or "",
        "characterName": char.name,
        "trees": {str(x)[-3:]: True for x in (avatar.get("trees") or [])},
        "talent": _talent(avatar, char, game),
        "attr": {k: v for k, v in attr.items() if not k.startswith("_")},
        "element": element,
        "weaponTypeName": _weapon_type_name(char.get("weapon"), game),
        "weapon": {
            "name": weapon.get("name") or "",
            "affix": int(weapon.get("affix") or 1),
            "level": int(weapon.get("level") or 1),
        },
        "weaponTables": (w_meta.get("skill") or {}).get("tables") or {},
        "sets": artis_set_data(pieces, game)["sets"],
        "enemyLv": 103 if game == "gs" else 80,
        "idx": int(idx) if idx is not None else None,
    }

    rule_code = meta._strip_esm(rule.read_text(encoding="utf-8"))
    script = _build_script(game, w_meta.get("type"), rule_code, payload)
    try:
        raw = eval_js(script)
        ret = json.loads(raw)
    except Exception as exc:  # JS 引擎的报错对维护者有用，对用户无意义
        raise DamageError(f"{char.name} 的伤害规则执行失败") from exc

    if ret.get("error") == "idx":
        raise DamageError(ret.get("message") or "序号输入错误")
    rows = ret.get("ret") or []
    if not rows:
        raise DamageError(f"{char.name} 暂无可用伤害条目")
    selected = ret.get("selected") or rows[0]
    return {
        "character": char.name,
        "game": game,
        "dmgMsg": ret.get("msg") or [],
        "dmgData": rows,
        "dmgRet": ret.get("dmgRet") or [],
        "selected": selected,
        "selectedIdx": ret.get("selectedIdx"),
        "mainAttr": ret.get("mainAttr") or "atk,cpct,cdmg",
    }


_GS_WEAPON_TYPE_NAME = {
    "sword": "单手剑",
    "catalyst": "法器",
    "bow": "弓",
    "claymore": "双手剑",
    "polearm": "长柄武器",
}


def _weapon_type_name(weapon_type: str | None, game: str) -> str:
    """Character.weaponTypeName：星铁为命途名，原神映射为中文武器类型"""
    if game == "sr":
        return weapon_type or ""
    return _GS_WEAPON_TYPE_NAME.get(str(weapon_type or "").lower(), "")
