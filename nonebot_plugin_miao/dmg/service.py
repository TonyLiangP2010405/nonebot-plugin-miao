"""Damage-calculation orchestration for upstream miao ``calc.js`` rules."""
from __future__ import annotations

import json
import re
from typing import Any

import quickjs

from ..core import meta
from ..core.attr_calc import calc_attr, elem_name
from .js_runtime import RUNTIME


class DamageError(ValueError):
    """A user-safe damage calculation error."""


def _number(value: Any, game: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    # 原神 talent 表是百分数，星铁表已是倍率小数。
    return float(match.group()) if game == "gs" else float(match.group())


def _talent(avatar: dict[str, Any], char: meta.CharacterMeta, game: str) -> dict[str, dict[str, float]]:
    levels = avatar.get("talent") or {}
    ret: dict[str, dict[str, float]] = {}
    for key, detail in (char.get("talent") or {}).items():
        level = levels.get(key, 1)
        level = int(level.get("level", 1) if isinstance(level, dict) else level or 1)
        table = detail.get("tables") or {}
        rows = table.values() if isinstance(table, dict) else table
        values: dict[str, float] = {}
        for row in rows:
            data = row.get("values") or []
            if data:
                values[str(row.get("name") or "")] = _number(data[min(max(level - 1, 0), len(data) - 1)], game)
        ret[key] = values
    return ret


def _rule_file(char: meta.CharacterMeta, avatar: dict[str, Any], game: str):
    name = char.name
    if game == "gs" and name == "旅行者":
        name = f"旅行者/{avatar.get('elem') or char.get('elem') or 'anemo'}"
    path = meta.RES_DIR / f"meta-{game}" / "character" / name / "calc.js"
    return path if path.is_file() else None


def calc_dmg(avatar: dict[str, Any], game: str, idx: int | None = None) -> dict[str, Any]:
    """Evaluate an upstream character rule and return its damage rows.

    ``idx`` is one-based, matching ``#角色伤害N``.  Rules, callbacks and
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
    payload = {
        "game": game,
        "level": int(avatar.get("level") or 90),
        "cons": int(avatar.get("cons") or 0),
        "trees": {str(x)[-3:]: True for x in (avatar.get("trees") or [])},
        "talent": _talent(avatar, char, game),
        "staticAttr": attr.get("staticAttr") or {},
        "element": elem_name(avatar.get("elem") or char.get("elem") or "", game),
        "weaponTypeName": char.get("weapon") or "",
        "refine": int(weapon.get("affix") or 1),
        "weapon": {"name": weapon.get("name") or "", "affix": int(weapon.get("affix") or 1)},
        "enemyLv": 103,
        "params": {},
    }
    code = meta._strip_esm(rule.read_text(encoding="utf-8"))
    ctx = quickjs.Context()
    try:
        raw = ctx.eval(f"{RUNTIME}\n{code}\n__miao_run({json.dumps(payload, ensure_ascii=False)})")
        ret = json.loads(raw)
    except Exception as exc:  # QuickJS messages are useful to maintainers, not users.
        raise DamageError(f"{char.name} 的伤害规则执行失败") from exc
    rows = ret.get("ret") or []
    if not rows:
        raise DamageError(f"{char.name} 暂无可用伤害条目")
    selected = None
    if idx is not None:
        if idx < 1 or idx > len(rows):
            raise DamageError(f"序号输入错误：{char.name}最多支持 {len(rows)} 种伤害计算")
        selected = rows[idx - 1]
    else:
        default = int(ret.get("defDmgIdx") or -1)
        selected = rows[default] if 0 <= default < len(rows) else rows[0]
    return {
        "character": char.name,
        "game": game,
        "dmgMsg": ret.get("msg") or [],
        "dmgData": rows,
        "dmgRet": [],
        "selected": selected,
        "mainAttr": ret.get("mainAttr") or "atk,cpct,cdmg",
    }
