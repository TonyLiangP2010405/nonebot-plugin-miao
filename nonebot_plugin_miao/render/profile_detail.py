# ruff: noqa: E501
"""角色详细面板卡片（信息层级对齐 miao-plugin 的 profile-detail）。

包含：立绘头部（等级/命座/天赋/属性权重）、武器卡（等级/精炼/副属性/被动）、
圣遗物汇总（总分/评级/词条统计）、逐件圣遗物（主副词条/强化次数/单件评分）与
伤害计算表（暴击/期望伤害、Buff 列表）。
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any

from ..core import meta
from ..core.artis_mark import format_value, key_title
from ..core.attr_calc import elem_name
from . import base

W, M = 960, 22
HEADER_H = 452
INNER = W - M * 2

# 属性行：(key, 是否百分比)
_GS_STATS = ("hp", "atk", "def", "mastery", "cpct", "cdmg", "recharge", "dmg")
_SR_STATS = ("hp", "atk", "def", "speed", "cpct", "cdmg", "recharge", "stance", "dmg", "heal")
_PCT_KEYS = {"cpct", "cdmg", "recharge", "dmg", "phy", "stance", "effPct", "effDef", "heal", "joy"}
_ATTR_LABEL = {
    "hp": "生命值",
    "atk": "攻击力",
    "def": "防御力",
    "mastery": "元素精通",
    "speed": "速度",
    "cpct": "暴击率",
    "cdmg": "暴击伤害",
    "recharge": "元素充能",
    "dmg": "伤害加成",
    "phy": "物理伤害",
    "stance": "击破特攻",
    "effPct": "效果命中",
    "effDef": "效果抵抗",
    "heal": "治疗加成",
    "joy": "欢愉度",
}
_CLASS_COLOR = {
    "D": "#9aa3b5",
    "C": "#6fcf97",
    "B": "#56a3f0",
    "A": "#b07cf0",
    "S": "#ffd166",
    "SS": "#ff8f5e",
    "SSS": "#ff5e7a",
    "ACE": "#ff3b6b",
    "MAX": "#ff2e63",
}
_WEAPON_ATTR_TITLE = {
    "atkPct": "攻击力",
    "hpPct": "生命值",
    "defPct": "防御力",
    "mastery": "元素精通",
    "cpct": "暴击率",
    "cdmg": "暴击伤害",
    "recharge": "元素充能",
    "dmg": "伤害加成",
    "phy": "物理伤害",
    "heal": "治疗加成",
    "shield": "护盾强效",
}
_GREAT = "#ffde9d"
_USEFUL = "#dfe8f5"
_NOUSE = "#8a94a6"


def _fmt_num(value: Any) -> str:
    return f"{value:.0f}" if isinstance(value, (int, float)) else "-"


def dmg_row_text(row: dict[str, Any]) -> str:
    """伤害条目文案。

    治疗 / 护盾 / 反应 / 击破类条目在上游规则里只返回 ``avg``（没有暴击值），
    不能按普通输出条目格式化 ``row['dmg']``，否则会 KeyError。
    """
    title = row.get("title") or ""
    avg = row.get("avg")
    crit = row.get("dmg")
    if crit is None:
        return f"{title}：{_fmt_num(avg)}"
    if avg is None:
        return f"{title}：暴击 {_fmt_num(crit)}"
    return f"{title}：期望 {_fmt_num(avg)} / 暴击 {_fmt_num(crit)}"


# ---------------------------------------------------------------------------
# 展示数据组装（纯函数，便于测试）
# ---------------------------------------------------------------------------


def _char(avatar: dict[str, Any], game: str) -> Any:
    return meta.get_character(avatar.get("id") or avatar.get("name") or "", game)


def _char_name(avatar: dict[str, Any], game: str) -> str:
    char = _char(avatar, game)
    return char.name if char else str(avatar.get("name") or "")


def _weight_color(weight: float) -> str:
    if weight >= 80:
        return _GREAT
    if weight > 0:
        return _USEFUL
    return _NOUSE


def _talent_level(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("level")
    try:
        return int(value or 1)
    except (TypeError, ValueError):
        return 1


def talent_items(avatar: dict[str, Any], game: str) -> list[dict[str, Any]]:
    """天赋图标 + 等级：[{key,label,level,img}]"""
    name = _char_name(avatar, game)
    talents = avatar.get("talent") or {}
    keys = [("a", "普攻"), ("e", "战技"), ("q", "爆发")] if game == "gs" else [
        ("a", "普攻"),
        ("e", "战技"),
        ("q", "终结技"),
        ("t", "天赋"),
    ]
    ret = []
    for key, label in keys:
        level = talents.get(key)
        if level is None and game == "sr" and key == "t":
            continue
        try:
            img = meta.talent_img(name, key, game)
        except ValueError:
            img = ""
        ret.append({"key": key, "label": label, "level": _talent_level(level), "img": img})
    return ret


def cons_items(avatar: dict[str, Any], game: str) -> list[dict[str, Any]]:
    """6 个命座图标（超出命座数的标记为未点亮）"""
    name = _char_name(avatar, game)
    cons = int(avatar.get("cons") or 0)
    ret = []
    for idx in range(1, 7):
        try:
            img = meta.cons_img(name, idx, game)
        except ValueError:
            img = ""
        ret.append({"idx": idx, "img": img, "on": idx <= cons})
    return ret


def stat_rows(avatar: dict[str, Any], attr: dict[str, Any], mark: dict[str, Any], game: str) -> list[dict[str, Any]]:
    """属性行：值 / 白值+加成 / 词条权重"""
    keys = _GS_STATS if game == "gs" else _SR_STATS
    cw = mark.get("charWeight") or {}
    dmg_key = "dmg"
    if game == "gs" and (attr.get("phy") or 0) > (attr.get("dmg") or 0):
        dmg_key = "phy"
    rows = []
    for key in keys:
        real = dmg_key if key == "dmg" else key
        value = float(attr.get(real) or 0)
        base_v = float(attr.get(f"{real}Base") or 0)
        pct = real in _PCT_KEYS
        if pct:
            value_text = f"{value:.1f}%"
            sub_text = f"{base_v:.1f} +{value - base_v:.1f}"
        else:
            value_text = f"{value:,.0f}"
            sub_text = f"{base_v:,.0f} +{value - base_v:,.0f}"
        label = _ATTR_LABEL.get(real if key == "dmg" else key, real)
        if game == "sr" and real == "recharge":
            label = "能量恢复效率"
        rows.append(
            {
                "key": real,
                "label": label,
                "value": value_text,
                "sub": sub_text,
                "weight": float(cw.get(real) or 0),
            }
        )
    return rows


def aggregate_attrs(mark: dict[str, Any], game: str) -> list[dict[str, Any]]:
    """所有圣遗物副词条的合计（对齐 Artis.getAllAttr：按 eff 降序取前 9）"""
    acc: dict[str, dict[str, Any]] = {}
    for piece in (mark.get("artis") or {}).values():
        for a in piece.get("attrs") or []:
            key = a.get("key")
            if not key:
                continue
            row = acc.setdefault(key, {"key": key, "value": 0.0, "upNum": 0, "eff": 0.0})
            row["value"] += float(a.get("value") or 0)
            row["upNum"] += int(a.get("upNum") or 0)
            row["eff"] += float(a.get("eff") or 0)
    rows = sorted(acc.values(), key=lambda r: r["eff"], reverse=True)[:9]
    cw = mark.get("charWeight") or {}
    for row in rows:
        row["eff"] = row["eff"] / (0.85 if game == "gs" else 0.9)
        row["weight"] = float(cw.get(row["key"]) or 0)
        row["title"] = key_title(row["key"], game)
        row["text"] = format_value(row["key"], row["value"], game)
    return rows


def artis_rows(mark: dict[str, Any], game: str) -> list[dict[str, Any]]:
    """逐件圣遗物展示数据"""
    cw = mark.get("charWeight") or {}
    rows = []
    for idx in sorted(mark.get("artis") or {}):
        piece = mark["artis"][idx]
        main = piece.get("main") or {}
        img = ""
        if piece.get("set"):
            try:
                img = meta.artis_img(piece["set"], idx, game)
            except ValueError:
                img = ""
        subs = []
        for a in piece.get("attrs") or []:
            key = a.get("key")
            if not key:
                continue
            subs.append(
                {
                    "title": key_title(key, game),
                    "value": format_value(key, float(a.get("value") or 0), game),
                    "weight": float(cw.get(key) or 0),
                    "upNum": int(a.get("upNum") or 0),
                }
            )
        rows.append(
            {
                "idx": idx,
                "name": piece.get("name") or "",
                "set": piece.get("set") or "",
                "level": int(piece.get("level") or 0),
                "mark": float(piece.get("mark") or 0),
                "markClass": piece.get("markClass"),
                "img": img,
                "main": {
                    "title": key_title(main.get("key"), game),
                    "value": format_value(main.get("key"), float(main.get("value") or 0), game),
                }
                if main.get("key")
                else None,
                "subs": subs,
            }
        )
    return rows


def _calc_gs_weapon(wm: dict[str, Any], level: int, promote: int) -> tuple[float, str, float] | None:
    """移植 miao Weapon.calcAttr（原神）：基础攻击 + 副属性词条（按等级插值）"""
    attr = wm.get("attr") or {}
    atk = attr.get("atk") or {}
    if not atk:
        return None
    steps = [1, 20, 40, 50, 60, 70, 80, 90]
    level = min(max(level, 1), steps[-1])
    lv_left, lv_right = 1, 20
    found = False
    for i in range(len(steps) - 1):
        if promote == i and steps[i] <= level <= steps[i + 1]:
            lv_left, lv_right, found = steps[i], steps[i + 1], True
            break
    if not found:
        for i in range(len(steps) - 1):
            if steps[i] <= level <= steps[i + 1]:
                lv_left, lv_right = steps[i], steps[i + 1]
                break
    left = atk.get(f"{lv_left}+") or atk.get(str(lv_left))
    right = atk.get(str(lv_right))
    if left is None or right is None:
        return None
    atk_base = left + (right - left) * (level - lv_left) / (lv_right - lv_left)
    bonus_key = str(attr.get("bonusKey") or "")
    bonus = attr.get("bonusData") or {}
    bl = bonus.get(f"{lv_left}+") or bonus.get(str(lv_left))
    br = bonus.get(str(lv_right))
    value = 0.0
    if bl is not None and br is not None and bonus_key:
        step_count = math.ceil((lv_right - lv_left) / 5)
        step = (br - bl) / step_count
        value = bl + (step_count - math.ceil((lv_right - level) / 5)) * step
    return atk_base, bonus_key, value


def _calc_sr_weapon(wm: dict[str, Any], level: int, promote: int) -> dict[str, float]:
    """移植 miao Weapon.calcAttr（星铁）：晋阶基础值 + 每级成长"""
    attr = wm.get("attr") or {}
    if not attr:
        return {}
    level = min(max(level, 1), 80)
    keys = sorted(attr, key=lambda k: int(k))
    cfg = attr.get(str(promote)) or attr.get(keys[-1])
    ret = {k: float(v) for k, v in (cfg.get("attrs") or {}).items()}
    for k, v in (wm.get("growAttr") or {}).items():
        ret[k] = ret.get(k, 0.0) + float(v) * (level - 1)
    return ret


def weapon_desc(wm: dict[str, Any], affix: int, game: str) -> str:
    """武器被动描述：替换精炼占位符并去掉 HTML 标签"""
    if game == "gs":
        affix_data = wm.get("affixData") or {}
        text = affix_data.get("text") or ""
        datas = affix_data.get("datas") or {}

        def repl(m: re.Match) -> str:
            vals = datas.get(m.group(1)) or []
            return str(vals[affix - 1]) if 0 < affix <= len(vals) else m.group(0)

        text = re.sub(r"\$\[(\d)\]", repl, text)
    else:
        skill = wm.get("skill") or {}
        text = skill.get("desc") or ""
        tables = skill.get("tables") or {}

        def repl2(m: re.Match) -> str:
            vals = tables.get(m.group(1)) or []
            if not 0 < affix <= len(vals):
                return m.group(0)
            val = float(vals[affix - 1])
            fmt = m.group(2)
            if m.group(3) == "%":
                return f"{val:.2f}%" if fmt == "f2" else f"{val:.1f}%"
            return f"{round(val)}"

        text = re.sub(r"\$(\d)\[(i|f1|f2)\](\%?)", repl2, text)
    return re.sub(r"<[^>]+>", "", text or "").strip()


def weapon_display(avatar: dict[str, Any], game: str) -> dict[str, Any] | None:
    """武器/光锥展示数据：图标、星级、精炼、等级、属性、被动"""
    weapon = avatar.get("weapon") or {}
    name = str(weapon.get("name") or "")
    if not name:
        return None
    wm = meta.get_weapon(name, game) or {}
    level = int(weapon.get("level") or 1)
    affix = int(weapon.get("affix") or 1)
    promote = int(weapon.get("promote") or 0)
    attrs: list[tuple[str, str]] = []
    try:
        icon = meta.weapon_img(name, "icon", game)
    except ValueError:
        icon = ""
    if game == "gs":
        calc = _calc_gs_weapon(wm, level, promote)
        if calc:
            atk_base, bonus_key, bonus_value = calc
            attrs.append(("攻击力", f"{atk_base:.0f}"))
            if bonus_key:
                title = _WEAPON_ATTR_TITLE.get(bonus_key) or key_title(bonus_key, game)
                attrs.append((title, f"{bonus_value:.1f}%" if bonus_key.endswith("Pct") or bonus_key in _PCT_KEYS else f"{bonus_value:.0f}"))
    else:
        stats = _calc_sr_weapon(wm, level, promote)
        for key in ("hp", "atk", "def"):
            if key in stats:
                attrs.append((_ATTR_LABEL[key], f"{stats[key]:.0f}"))
    return {
        "name": name,
        "icon": icon,
        "star": int(wm.get("star") or 4),
        "level": level,
        "affix": affix,
        "affix_label": f"{'精' if game == 'gs' else '叠'}{affix}",
        "attrs": attrs,
        "desc": weapon_desc(wm, affix, game),
    }




_TRACE_LAYOUT = (0, 1, 1, 3, 3, 5, 5, 7, 7, 8)
_TRACE_TALENT_POS = (2, 4, 6)


def trace_columns(avatar: dict[str, Any], game: str) -> list[list[dict[str, Any]]]:
    """星铁行迹图标列（9 列：属性节点 1-2 个/列，第 2/4/6 列为三大行迹）"""
    if game != "sr":
        return []
    char = _char(avatar, game)
    tree_meta = (char.get("tree") if char else {}) or {}
    if not char or not tree_meta:
        return []
    unlocked = {str(x)[-3:] for x in (avatar.get("trees") or [])}
    cols: list[list[dict[str, Any]]] = [[] for _ in range(9)]
    keys = sorted(tree_meta, key=lambda k: int(k))
    for i, node_id in enumerate(keys[: len(_TRACE_LAYOUT)]):
        cfg = tree_meta.get(node_id) or {}
        key = cfg.get("key")
        if not key:
            continue
        cols[_TRACE_LAYOUT[i]].append(
            {
                "img": f"meta-sr/public/icons/tree-{key}.webp",
                "on": str(node_id)[-3:] in unlocked,
                "title": key,
            }
        )
    for idx, pos in enumerate(_TRACE_TALENT_POS):
        cols[pos] = [
            {
                "img": meta.char_img(char.name, f"tree-{idx + 1}", game),
                "on": f"{idx + 101}" in unlocked,
                "title": f"行迹{idx + 1}",
            }
        ]
    return cols


def damage_matrix(dmg: dict[str, Any], game: str) -> dict[str, Any] | None:
    """词条变化矩阵：行=降低的属性，列=提高的属性（对齐 miao 的 dmgRet 热力表）"""
    keys = [k for k in str(dmg.get("mainAttr") or "").split(",") if k]
    rows = dmg.get("dmgRet") or []
    if len(keys) < 2 or not rows:
        return None
    attr_map = meta.artifact_extra(game).get("attrMap") or {}
    headers = [
        {
            "key": key,
            "title": (attr_map.get(key) or {}).get("title") or key,
            "text": (attr_map.get(key) or {}).get("text") or "",
        }
        for key in keys
    ]
    base = dmg.get("selected") or {}
    base_avg = base.get("avg")
    base_dmg = base.get("dmg")
    out = []
    for row in rows:
        cells = []
        for cell in row[: len(keys)]:
            item: dict[str, Any] = {"type": cell.get("type") or "avg"}
            if item["type"] != "na":
                avg = cell.get("avg")
                crit = cell.get("dmg")
                item["avg"] = avg
                item["crit"] = crit
                item["val"] = avg - base_avg if isinstance(avg, (int, float)) and isinstance(base_avg, (int, float)) else None
            cells.append(item)
        out.append(cells)
    return {"headers": headers, "rows": out, "base": {"avg": base_avg, "dmg": base_dmg}}


# ---------------------------------------------------------------------------
# 绘制辅助
# ---------------------------------------------------------------------------


def _text(canvas: Any, text: Any, x: float, y: float, size: float = 14, c: str = base.TEXT_MAIN, kind: str = "default") -> None:
    base.draw_text(canvas, text, x, y, base.font(size, kind), base.color(c))


def _text_right(canvas: Any, text: Any, right: float, y: float, size: float = 14, c: str = base.TEXT_MAIN, kind: str = "default") -> None:
    f = base.font(size, kind)
    base.draw_text(canvas, text, right - base.measure(str(text), f), y, f, base.color(c))


def _ellipsis(canvas: Any, text: Any, x: float, y: float, max_w: float, size: float = 14, c: str = base.TEXT_MAIN) -> None:
    base.draw_text_ellipsis(canvas, text, x, y, max_w, base.font(size), base.color(c))


def _hgrad(canvas: Any, x: float, y: float, w: float, h: float, colors: list[int]) -> None:
    import skia

    paint = skia.Paint(AntiAlias=True)
    paint.setShader(skia.GradientShader.MakeLinear([(x, y), (x + w, y)], colors, None, skia.TileMode.kClamp))
    canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint)


def _fmt_int(value: Any) -> str:
    return f"{value:,.0f}" if isinstance(value, (int, float)) else "-"


def _pair(avg: Any, crit: Any) -> str:
    return f"{_fmt_int(avg)}/{_fmt_int(crit)}" if crit is not None else _fmt_int(avg)


def _wrap(text: str, f: Any, max_w: float, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    cur = ""
    for ch in str(text or ""):
        if base.measure(cur + ch, f) > max_w and cur:
            lines.append(cur)
            cur = ch
            if len(lines) >= max_lines:
                break
        else:
            cur += ch
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if lines:
        lines[-1] = base.ellipsize(lines[-1], f, max_w)
    return lines


def _section(canvas: Any, y: float, title: str, right: str = "") -> None:
    base.draw_rounded(canvas, M, y - 15, 4, 18, 2, base.color(base.GOLD))
    _text(canvas, title, M + 12, y, 19, base.TEXT_MAIN, "title")
    if right:
        _text_right(canvas, right, W - M, y, 13, base.TEXT_SUB)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def render_profile_detail(
    avatar: dict, attr: dict, mark: dict, game: str, dmg: dict | None = None, uid: str = ""
) -> bytes:
    """渲染角色详情卡（属性 / 天赋命座 / 武器 / 圣遗物 / 伤害）"""
    char = _char(avatar, game)
    name = char.name if char else str(avatar.get("name") or "未知")
    artis_word = "圣遗物" if game == "gs" else "遗器"
    elem = avatar.get("elem") or (char.get("elem") if char else "")
    level = int(avatar.get("level") or 0)
    cons = int(avatar.get("cons") or 0)
    talents = talent_items(avatar, game)
    cons_list = cons_items(avatar, game)
    stats = stat_rows(avatar, attr, mark, game)
    all_attr = aggregate_attrs(mark, game)
    pieces = artis_rows(mark, game)
    weapon = weapon_display(avatar, game)
    trace_cols = trace_columns(avatar, game)
    trace_total = sum(len(col) for col in trace_cols)
    trace_lit = sum(1 for col in trace_cols for item in col if item["on"])
    matrix = damage_matrix(dmg, game) if dmg else None

    # 统一收集图片，避免渲染时同步等待
    paths: list[str] = []

    def add(path: str) -> None:
        if path and path not in paths:
            paths.append(path)

    add(meta.char_img(name, "splash", game))
    for item in talents:
        add(item["img"])
    for item in cons_list:
        add(item["img"])
    for piece in pieces:
        add(piece["img"])
    for col in trace_cols:
        for item in col:
            add(item["img"])
    if weapon:
        add(weapon["icon"])
    fetched = await asyncio.gather(*(base.fetch_image(p) for p in paths))
    images = dict(zip(paths, fetched))

    def img(path: str) -> Any:
        return images.get(path)

    weapon_h = 128 if weapon and weapon.get("desc") else 100
    score_text = f"{mark.get('mark') or 0:.1f}"
    score_class = mark.get("markClass") or "-"
    source = avatar.get("_source") or ""
    stamp = avatar.get("_time") or 0
    time_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(stamp))) if stamp else ""

    def builder(canvas: Any, w: int, h: int) -> int:
        if h:
            # 详情卡整卡深色底；否则 PNG 透明区域在浅色客户端上会把白字吞掉
            base.draw_gradient(
                canvas,
                0,
                0,
                w,
                h,
                [base.color("#171A21"), base.color("#0E1015")],
                diagonal=False,
            )
            base.draw_gradient(canvas, 0, 0, w, HEADER_H, base.elem_gradient(elem), diagonal=False)
            splash = img(meta.char_img(name, "splash", game))
            if splash is not None:
                base.draw_image_cover(canvas, splash, 0, 0, 600, HEADER_H)
            _hgrad(
                canvas,
                430,
                0,
                w - 430,
                HEADER_H,
                [base.color("#00121418"), base.color("#E6121418")],
            )
            # 信息面板
            px = 540
            pw = w - M - px
            base.draw_rounded(canvas, px - 16, 16, pw + 16, HEADER_H - 32, 14, base.color("#4D000000"))
            _ellipsis(canvas, name, px, 70, pw - 10, 40, base.TEXT_MAIN)
            sub = f"Lv.{level} · C{cons} · {elem_name(elem, game) or '—'}"
            if uid:
                sub = f"UID {uid} · " + sub
            _ellipsis(canvas, sub, px, 98, pw - 10, 14, base.TEXT_SUB)
            _text(canvas, f"{artis_word}评分", px, 126, 12, base.TEXT_SUB)
            _text(canvas, score_text, px + 72, 129, 22, base.NUM_GOLD, "number")
            _text(canvas, score_class, px + 72 + base.measure(score_text, base.font(22, "number")) + 10, 128, 16, _CLASS_COLOR.get(score_class, base.TEXT_SUB))

            # 天赋
            ty = 150
            _text(canvas, "天赋", px, ty + 29, 12, base.TEXT_SUB)
            for i, item in enumerate(talents):
                ix = px + 44 + i * 60
                base.draw_rounded(canvas, ix, ty, 50, 50, 8, base.color("#66000000"))
                base.draw_image_or_placeholder(canvas, img(item["img"]), (ix + 3, ty + 3, 44, 44), item["label"], 6)
                _text(canvas, f"Lv{item['level']}", ix + 25, ty + 66, 11, base.TEXT_MAIN)

            # 命座
            cy = 238
            _text(canvas, "命座", px, cy + 20, 12, base.TEXT_SUB)
            for i, item in enumerate(cons_list):
                ix = px + 44 + i * 40
                base.draw_rounded(canvas, ix, cy, 32, 32, 6, base.color("#66000000"))
                base.draw_image_or_placeholder(canvas, img(item["img"]), (ix + 2, cy + 2, 28, 28), str(item["idx"]), 4)
                if not item["on"]:
                    base.draw_rounded(canvas, ix, cy, 32, 32, 6, base.color("#B3141619"))

            # 属性（原神 2×4；星铁 2×5，额外展示击破特攻/治疗加成）
            sx = px
            cell_w = (pw - 8) / 2
            grid_y = 278 if game == "sr" else 286
            row_h = 31 if game == "sr" else 38
            for i, row in enumerate(stats[: 10 if game == "sr" else 8]):
                col, line = i % 2, i // 2
                x = sx + col * (cell_w + 8)
                y = grid_y + line * row_h
                if line:
                    base.draw_rounded(canvas, x, y - 20, cell_w, 1, 0, base.color("#1AFFFFFF"))
                _text(canvas, row["label"], x, y, 12, base.TEXT_SUB)
                if row["weight"] and h:
                    wl = base.measure(row["label"], base.font(12))
                    wt = f"{row['weight']:.0f}"
                    ww = base.measure(wt, base.font(10)) + 8
                    base.draw_rounded(canvas, x + wl + 5, y - 11, ww, 14, 7, base.color("#66000000"))
                    _text(canvas, wt, x + wl + 9, y - 1, 10, _weight_color(row["weight"]))
                _text_right(canvas, row["value"], x + cell_w, y + 2, 15, base.TEXT_MAIN)
                _text_right(canvas, row["sub"], x + cell_w, y + 16, 10, base.TEXT_SUB)

        y = HEADER_H + 22

        # ---------------- 星铁行迹 ----------------
        if trace_cols:
            if h:
                _section(canvas, y, "行迹", f"已点亮 {trace_lit}/{trace_total}")
            y += 26
            trace_rows = max(len(col) for col in trace_cols)
            if h:
                for i, col in enumerate(trace_cols):
                    x = M + i * 48
                    for j, item in enumerate(col):
                        yy = y + j * 36
                        base.draw_rounded(canvas, x, yy, 32, 32, 7, base.color("#26FFFFFF"))
                        base.draw_rounded(canvas, x + 1, yy + 1, 30, 30, 6, base.color("#40000000"))
                        base.draw_image_or_placeholder(canvas, img(item["img"]), (x + 2, yy + 2, 28, 28), item["title"], 5)
                        if not item["on"]:
                            base.draw_rounded(canvas, x, yy, 32, 32, 7, base.color("#B3141619"))
            y += trace_rows * 36 + 10

        # ---------------- 圣遗物 ----------------
        total = f"总分 {score_text}  {score_class}    规则 {mark.get('classTitle') or '—'}"
        if h:
            _section(canvas, y, artis_word, total)
        y += 22

        # 副词条合计
        if all_attr:
            chip_w_total = 0
            for row in all_attr:
                title, val = row["title"], row["text"]
                chip_w_total += base.measure(title, base.font(12)) + base.measure(f"+{val}", base.font(12)) + 34
            if chip_w_total > INNER:
                # 两行展示
                rows = [all_attr[: (len(all_attr) + 1) // 2], all_attr[(len(all_attr) + 1) // 2:]]
            else:
                rows = [all_attr]
            if h:
                for r_i, chip_rows in enumerate(rows):
                    x = M
                    for row in chip_rows:
                        title, val = row["title"], row["text"]
                        cw_ = base.measure(title, base.font(12)) + base.measure(f"+{val}", base.font(12)) + 26
                        base.draw_rounded(canvas, x, y + r_i * 32, cw_, 26, 13, base.color("#66000000"))
                        _text(canvas, title, x + 10, y + r_i * 32 + 17, 12, base.TEXT_SUB)
                        _text(canvas, f"+{val}", x + 10 + base.measure(title, base.font(12)) + 6, y + r_i * 32 + 17, 12, _weight_color(row["weight"]))
                        x += cw_ + 8
            y += 32 * len(rows) + 6

        # 武器
        if weapon:
            if h:
                base.draw_rounded(canvas, M, y, INNER, weapon_h, 10, base.color("#4D000000"))
                base.draw_image_or_placeholder(canvas, img(weapon["icon"]), (M + 12, y + 22, 64, 64), weapon["name"], 8)
                wx = M + 90
                _ellipsis(canvas, weapon["name"], wx, y + 32, 210, 18)
                _text(canvas, f"{weapon['affix_label']} · Lv{weapon['level']}", wx, y + 54, 12, base.TEXT_SUB)
                if weapon["star"]:
                    base.draw_stars(canvas, wx + 52, y + 74, weapon["star"], 5, 12)
                ax = M + 330
                for i, (label, value) in enumerate(weapon["attrs"][:3]):
                    _text(canvas, label, ax, y + 36 + i * 24, 12, base.TEXT_SUB)
                    _text(canvas, value, ax + 92, y + 36 + i * 24, 13, base.TEXT_MAIN)
                if weapon.get("desc"):
                    lines = _wrap(weapon["desc"], base.font(12), INNER - 104, 2)
                    for i, line in enumerate(lines):
                        _text(canvas, line, wx, y + 98 + i * 16, 12, base.TEXT_SUB)
            y += weapon_h + 12

        # 逐件圣遗物
        if not pieces:
            if h:
                base.draw_rounded(canvas, M, y, INNER, 44, 8, base.color("#33000000"))
                _text(canvas, "暂无圣遗物数据（展示柜中该角色未装备或未公开圣遗物）", M + 16, y + 28, 13, base.TEXT_SUB)
            y += 44
        for piece in pieces:
            if h:
                base.draw_rounded(canvas, M, y, INNER, 100, 10, base.color("#4D000000"))
                base.draw_image_or_placeholder(canvas, img(piece["img"]), (M + 12, y + 20, 60, 60), piece["name"], 8)
                nx = M + 86
                _ellipsis(canvas, piece["name"], nx, y + 32, 200, 16)
                nw = min(base.measure(piece["name"], base.font(16)), 190)
                _text(canvas, f"+{piece['level']}", nx + nw + 10, y + 32, 13, base.NUM_GOLD)
                _ellipsis(canvas, piece["set"], nx, y + 52, 210, 12, base.TEXT_SUB)
                cls = piece["markClass"]
                _text(canvas, f"{piece['mark']:.1f} 分", nx, y + 76, 13, _CLASS_COLOR.get(cls, base.TEXT_SUB))
                if cls:
                    _text(canvas, cls, nx + base.measure(f"{piece['mark']:.1f} 分", base.font(13)) + 8, y + 76, 13, _CLASS_COLOR.get(cls, base.TEXT_SUB))
                mx = M + 330
                if piece["main"]:
                    _text(canvas, piece["main"]["title"], mx, y + 34, 12, base.TEXT_SUB)
                    _text(canvas, f"+{piece['main']['value']}", mx, y + 58, 15, base.TEXT_MAIN)
                gx = M + 470
                for i, sub in enumerate(piece["subs"][:4]):
                    col, line = i % 2, i // 2
                    x = gx + col * 180
                    yy = y + 34 + line * 24
                    _ellipsis(canvas, f"{sub['title']} +{sub['value']}", x, yy, 165, 12, _weight_color(sub["weight"]))
                    if sub["upNum"]:
                        vw = base.measure(f"{sub['title']} +{sub['value']}", base.font(12))
                        for d in range(min(sub["upNum"], 5)):
                            base.draw_rounded(canvas, x + vw + 4 + d * 5, yy - 6, 3, 3, 1.5, base.color(_weight_color(sub["weight"])))
            y += 108

        # ---------------- 伤害 ----------------
        if dmg:
            y += 8
            enemy = 103 if game == "gs" else 80
            if h:
                _section(canvas, y, "伤害计算", f"目标 Lv{enemy} 小宝")
            y += 20
            rows = dmg.get("dmgData") or []
            if h:
                base.draw_rounded(canvas, M, y, INNER, 30, 8, base.color("#59000000"))
                _text(canvas, "#", M + 16, y + 20, 12, base.TEXT_SUB)
                _text(canvas, "伤害类型", M + 52, y + 20, 12, base.TEXT_SUB)
                _text_right(canvas, "暴击伤害", W - M - 190, y + 20, 12, base.TEXT_SUB)
                _text_right(canvas, "期望伤害", W - M - 24, y + 20, 12, base.TEXT_SUB)
            y += 30
            for i, row in enumerate(rows):
                if h:
                    if i % 2 == 0:
                        base.draw_rounded(canvas, M, y, INNER, 30, 0, base.color("#26000000"))
                    _text(canvas, str(i + 1), M + 16, y + 20, 12, base.TEXT_SUB)
                    _ellipsis(canvas, row.get("title") or "", M + 52, y + 20, 380, 13, base.TEXT_MAIN)
                    crit = row.get("dmg")
                    avg = row.get("avg")
                    _text_right(canvas, _fmt_int(crit), W - M - 190, y + 20, 13, base.NUM_GOLD if crit is not None else base.TEXT_SUB)
                    _text_right(canvas, _fmt_int(avg), W - M - 24, y + 20, 13, base.TEXT_MAIN)
                y += 30

            # 词条变化矩阵
            if matrix:
                y += 22
                if h:
                    _section(canvas, y, "词条伤害计算", f"当前：[{dmg.get('selected', {}).get('title') or ''}]")
                y += 18
                headers = matrix["headers"]
                ncol = len(headers)
                label_w = 132.0
                col_w = (INNER - label_w) / ncol
                if h:
                    base.draw_rounded(canvas, M, y, INNER, 44, 8, base.color("#59000000"))
                    _text(canvas, "词条变化", M + 12, y + 27, 12, base.TEXT_SUB)
                    for i, hd in enumerate(headers):
                        x = M + label_w + i * col_w
                        _text(canvas, hd["title"], x + 10, y + 20, 12, base.TEXT_MAIN)
                        _text(canvas, f"+{hd['text']}", x + 10, y + 35, 10, base.GOLD)
                y += 44
                base_pair = _pair(matrix["base"]["avg"], matrix["base"]["dmg"])
                for r_i, row in enumerate(matrix["rows"]):
                    if h:
                        if r_i % 2 == 0:
                            base.draw_rounded(canvas, M, y, INNER, 46, 0, base.color("#26000000"))
                        hd = headers[r_i]
                        _text(canvas, hd["title"], M + 12, y + 20, 12, base.TEXT_MAIN)
                        _text(canvas, f"-{hd['text']}", M + 12, y + 36, 10, base.TEXT_SUB)
                        for i, cell in enumerate(row):
                            x = M + label_w + i * col_w
                            if cell["type"] == "na":
                                _text(canvas, "-", x + 10, y + 20, 12, base.TEXT_SUB)
                                _text(canvas, base_pair, x + 10, y + 36, 10, base.TEXT_SUB)
                            else:
                                color = {"gt": "#7bd88f", "lt": "#ff8080"}.get(cell["type"], base.TEXT_SUB)
                                delta = cell.get("val")
                                delta_text = "-" if delta is None else (f"+{delta:,.0f}" if delta > 0 else f"{delta:,.0f}")
                                _text(canvas, delta_text, x + 10, y + 20, 12, color)
                                _text(canvas, _pair(cell.get("avg"), cell.get("crit")), x + 10, y + 36, 10, base.TEXT_SUB)
                    y += 46
                if h:
                    _text(canvas, "大数字为副词条替换后的平均伤害变化，小字为平均/暴击伤害；绿色提升、红色下降", M, y + 16, 10, base.TEXT_SUB)
                y += 26

            msgs = [m for m in (dmg.get("dmgMsg") or []) if m]
            if msgs:
                y += 10
                if h:
                    _text(canvas, "Buff 列表", M, y + 14, 13, base.GOLD)
                y += 22
                for msg in msgs:
                    if h:
                        _ellipsis(canvas, f"· {msg}", M + 6, y + 14, INNER - 12, 12, base.TEXT_SUB)
                    y += 20

        # ---------------- 页脚 ----------------
        y += 14
        if h:
            src_text = f"数据源 {source or '本地'}" + (f" · 更新 {time_text}" if time_text else "")
            _text(canvas, src_text, M, y, 11, base.TEXT_SUB)
            _text_right(canvas, "评分为喵喵版规则，仅供参考", W - M, y, 11, base.TEXT_SUB)
        return int(y + 18)

    return base.render_card(W, builder)
