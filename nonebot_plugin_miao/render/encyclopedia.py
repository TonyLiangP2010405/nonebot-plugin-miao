# ruff: noqa: E501
"""原神角色与武器图鉴卡片。"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any

import skia

from ..core import meta
from ..core.artis_mark import key_title
from ..core.attr_calc import elem_name
from . import base

W, M = 860, 24

_WEAPON_TYPE = {
    "sword": "单手剑",
    "claymore": "双手剑",
    "polearm": "长柄武器",
    "bow": "弓",
    "catalyst": "法器",
}
_ELEM_ORDER = ["pyro", "hydro", "anemo", "electro", "dendro", "cryo", "geo", "multi"]
_ELEM_TITLE = {
    "pyro": "火元素",
    "hydro": "水元素",
    "anemo": "风元素",
    "electro": "雷元素",
    "dendro": "草元素",
    "cryo": "冰元素",
    "geo": "岩元素",
    "multi": "多元素",
}
_ATTR_TITLE = {
    "hpBase": "生命值",
    "atkBase": "攻击力",
    "defBase": "防御力",
    "atkPct": "攻击力",
    "defPct": "防御力",
    "hpPct": "生命值",
    "cpct": "暴击率",
    "cdmg": "暴击伤害",
    "recharge": "元素充能效率",
    "mastery": "元素精通",
    "phy": "物理伤害加成",
}
_PERCENT_ATTRS = {"atkPct", "defPct", "hpPct", "cpct", "cdmg", "recharge", "phy"}


def _clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _wrap_text(text: str, font: skia.Font, max_width: float) -> list[str]:
    """按实际字宽换行，兼容没有空格的中文文本。"""
    lines: list[str] = []
    current = ""
    for char in str(text or ""):
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if current and base.measure(candidate, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return lines


def _draw_wrapped(canvas: skia.Canvas, text: str, x: float, y: float, width: float, font: skia.Font, color: int, line_height: float) -> float:
    for line in _wrap_text(text, font, width):
        base.draw_text(canvas, line, x, y, font, color)
        y += line_height
    return y


def _section_title(canvas: skia.Canvas, title: str, y: float) -> float:
    base.draw_text(canvas, title, M, y + 25, base.font(21, "title"), base.color(base.TEXT_MAIN))
    return y + 38


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "-")
    return str(round(number)) if number.is_integer() else f"{number:.1f}"


def character_level_stats(character: meta.CharacterMeta, level: str = "90") -> dict[str, Any]:
    """从角色元数据中提取指定等级的展示属性。"""
    attr = character.get("attr") or {}
    keys = attr.get("keys") or []
    values = (attr.get("details") or {}).get(level) or []
    if keys and values:
        return dict(zip(keys, values))
    base_attr = character.get("baseAttr") or {}
    return {f"{key}Base": value for key, value in base_attr.items()}


def weapon_level_stats(weapon: dict[str, Any]) -> tuple[int, Any, str, Any]:
    """返回武器最高等级、基础攻击、副属性名和值。"""
    attr = weapon.get("attr") or {}
    atk = attr.get("atk") or {}
    levels = sorted(int(key) for key in atk if str(key).isdigit())
    level = levels[-1] if levels else 1
    bonus_key = str(attr.get("bonusKey") or "")
    bonus = (attr.get("bonusData") or {}).get(str(level))
    return level, atk.get(str(level), 0), bonus_key, bonus


def weapon_affix_text(weapon: dict[str, Any], rank: int = 1) -> str:
    """把武器效果中的 $[n] 占位符替换为指定精炼等级的数值。"""
    affix = weapon.get("affixData") or {}
    text = str(affix.get("text") or "")
    for key, values in (affix.get("datas") or {}).items():
        values = list(values or [])
        value = values[min(max(rank, 1), len(values)) - 1] if values else "-"
        text = text.replace(f"$[{key}]", str(value))
    return text


async def render_character_encyclopedia(character: meta.CharacterMeta) -> bytes:
    """渲染原神角色图鉴，无需玩家面板数据。"""
    name = character.name
    splash, talent_a, talent_e, talent_q = await asyncio.gather(
        base.fetch_image(meta.char_img(name, "splash", "gs")),
        base.fetch_image(meta.char_img(name, "talent-a", "gs")),
        base.fetch_image(meta.char_img(name, "talent-e", "gs")),
        base.fetch_image(meta.char_img(name, "talent-q", "gs")),
    )
    talents = character.get("talent") or {}
    talent_rows = [(key, talents.get(key) or {}, image) for key, image in (("a", talent_a), ("e", talent_e), ("q", talent_q))]
    stats = character_level_stats(character)

    def builder(canvas: skia.Canvas, width: int, height: int) -> int:
        if height:
            base.draw_gradient(canvas, 0, 0, width, height, base.elem_gradient(character.get("elem")))
        y = float(M)
        base.draw_text(canvas, name, M, y + 38, base.font(34, "title"), base.color(base.TEXT_MAIN))
        title = character.get("title") or "原神角色"
        base.draw_text(canvas, title, M, y + 65, base.font(15), base.color(base.GOLD))
        base.draw_stars(canvas, width - 88, y + 34, int(character.get("star") or 0), 8)
        y += 86

        hero_h = 292
        base.draw_rounded(canvas, M, y, width - M * 2, hero_h, 14, base.color("#78000000"))
        base.draw_image_or_placeholder(canvas, splash, (M + 8, y + 8, 320, hero_h - 16), name, 10)
        x = M + 350
        info = [
            ("元素", _ELEM_TITLE.get(character.get("elem"), elem_name(character.get("elem"), "gs"))),
            ("武器", _WEAPON_TYPE.get(character.get("weapon"), str(character.get("weapon") or "-"))),
            ("所属", character.get("allegiance") or "-"),
            ("命之座", character.get("astro") or "-"),
            ("生日", character.get("birth") or "-"),
            ("中配", character.get("cncv") or "-"),
            ("日配", character.get("jpcv") or "-"),
        ]
        for index, (label, value) in enumerate(info):
            yy = y + 34 + index * 32
            base.draw_text(canvas, label, x, yy, base.font(14), base.color(base.TEXT_SUB))
            base.draw_text_ellipsis(canvas, value, x + 70, yy, width - x - 92, base.font(16), base.color(base.TEXT_MAIN))
        y += hero_h + 14

        desc = _clean_text(character.get("desc"))
        base.draw_rounded(canvas, M, y, width - M * 2, 70, 12, base.color("#68000000"))
        _draw_wrapped(canvas, desc, M + 14, y + 25, width - M * 2 - 28, base.font(15), base.color(base.TEXT_MAIN), 22)
        y += 84

        y = _section_title(canvas, "90级属性", y)
        base.draw_rounded(canvas, M, y, width - M * 2, 78, 12, base.color("#68000000"))
        display_stats = [(key, value) for key, value in stats.items() if key in _ATTR_TITLE]
        for index, (key, value) in enumerate(display_stats[:4]):
            x = M + 18 + index * ((width - M * 2 - 36) / 4)
            suffix = "%" if key in _PERCENT_ATTRS else ""
            base.draw_text(canvas, _ATTR_TITLE[key], x, y + 27, base.font(13), base.color(base.TEXT_SUB))
            base.draw_text(canvas, f"{_format_number(value)}{suffix}", x, y + 57, base.font(21, "number"), base.color(base.NUM_GOLD))
        y += 92

        materials = character.get("materials") or {}
        y = _section_title(canvas, "培养材料", y)
        material_text = "　".join(str(value) for value in materials.values() if value)
        base.draw_rounded(canvas, M, y, width - M * 2, 66, 12, base.color("#68000000"))
        _draw_wrapped(canvas, material_text, M + 14, y + 26, width - M * 2 - 28, base.font(14), base.color(base.TEXT_MAIN), 21)
        y += 80

        y = _section_title(canvas, "战斗天赋", y)
        for key, talent, image in talent_rows:
            if not talent:
                continue
            base.draw_rounded(canvas, M, y, width - M * 2, 62, 10, base.color("#68000000"))
            base.draw_image_or_placeholder(canvas, image, (M + 8, y + 7, 48, 48), talent.get("name") or key, 8)
            label = {"a": "普通攻击", "e": "元素战技", "q": "元素爆发"}[key]
            base.draw_text(canvas, label, M + 70, y + 24, base.font(13), base.color(base.TEXT_SUB))
            base.draw_text_ellipsis(canvas, talent.get("name") or "-", M + 70, y + 49, width - M * 2 - 86, base.font(17), base.color(base.TEXT_MAIN))
            y += 70

        passive_names = [str(item.get("name")) for item in (character.get("passive") or []) if item.get("name")]
        if passive_names:
            y = _section_title(canvas, "固有天赋", y + 2)
            base.draw_rounded(canvas, M, y, width - M * 2, 52, 10, base.color("#68000000"))
            base.draw_text_ellipsis(canvas, " · ".join(passive_names), M + 14, y + 33, width - M * 2 - 28, base.font(15), base.color(base.TEXT_MAIN))
            y += 66

        constellations = character.get("cons") or {}
        if constellations:
            y = _section_title(canvas, "命之座", y + 2)
            for number in ("1", "2", "3", "4", "5", "6"):
                item = constellations.get(number) or {}
                if not item:
                    continue
                base.draw_rounded(canvas, M, y, width - M * 2, 38, 8, base.color("#58000000"))
                base.draw_text(canvas, f"{number}命", M + 12, y + 25, base.font(14), base.color(base.NUM_GOLD))
                base.draw_text_ellipsis(canvas, item.get("name") or "-", M + 64, y + 25, width - M * 2 - 78, base.font(14), base.color(base.TEXT_MAIN))
                y += 44
        return int(y + M)

    return base.render_card(W, builder)


async def render_weapon_encyclopedia(weapon: dict[str, Any]) -> bytes:
    """渲染原神武器图鉴。"""
    name = str(weapon.get("name") or "未知武器")
    image = await base.fetch_image(meta.weapon_img(name, "gacha", "gs"))
    level, attack, bonus_key, bonus = weapon_level_stats(weapon)
    star = int(weapon.get("star") or 0)
    passive_r1 = weapon_affix_text(weapon, 1)
    passive_r5 = weapon_affix_text(weapon, 5)

    def builder(canvas: skia.Canvas, width: int, height: int) -> int:
        if height:
            end = base.STAR_COLORS.get(star, "#36577d")
            base.draw_gradient(canvas, 0, 0, width, height, [base.color("#171a28"), base.color(end)])
        y = float(M)
        base.draw_text(canvas, name, M, y + 38, base.font(34, "title"), base.color(base.TEXT_MAIN))
        base.draw_text(canvas, _WEAPON_TYPE.get(weapon.get("type"), str(weapon.get("type") or "武器")), M, y + 65, base.font(15), base.color(base.GOLD))
        base.draw_stars(canvas, width - 88, y + 34, star, 8)
        y += 86

        hero_h = 310
        base.draw_rounded(canvas, M, y, width - M * 2, hero_h, 14, base.color("#78000000"))
        base.draw_image_or_placeholder(canvas, image, (M + 8, y + 8, 330, hero_h - 16), name, 10)
        x = M + 366
        base.draw_text(canvas, f"Lv{level}", x, y + 48, base.font(16), base.color(base.TEXT_SUB))
        base.draw_text(canvas, "基础攻击力", x, y + 84, base.font(14), base.color(base.TEXT_SUB))
        base.draw_text(canvas, _format_number(attack), x, y + 120, base.font(30, "number"), base.color(base.NUM_GOLD))
        if bonus_key and bonus is not None:
            base.draw_text(canvas, _ATTR_TITLE.get(bonus_key, key_title(bonus_key, "gs")), x, y + 166, base.font(14), base.color(base.TEXT_SUB))
            suffix = "%" if bonus_key in _PERCENT_ATTRS else ""
            base.draw_text(canvas, f"{_format_number(bonus)}{suffix}", x, y + 202, base.font(28, "number"), base.color(base.NUM_GOLD))
        y += hero_h + 14

        desc = _clean_text(weapon.get("desc"))
        base.draw_rounded(canvas, M, y, width - M * 2, 70, 12, base.color("#68000000"))
        _draw_wrapped(canvas, desc, M + 14, y + 25, width - M * 2 - 28, base.font(15), base.color(base.TEXT_MAIN), 22)
        y += 84

        if passive_r1:
            y = _section_title(canvas, str(weapon.get("affixTitle") or "武器效果"), y)
            lines = _wrap_text(passive_r1, base.font(15), width - M * 2 - 28)
            box_h = 24 + len(lines) * 22 + (29 if passive_r5 != passive_r1 else 0)
            base.draw_rounded(canvas, M, y, width - M * 2, box_h, 12, base.color("#68000000"))
            base.draw_text(canvas, "精炼1", M + 14, y + 24, base.font(13), base.color(base.NUM_GOLD))
            text_y = y + 48
            for line in lines:
                base.draw_text(canvas, line, M + 14, text_y, base.font(15), base.color(base.TEXT_MAIN))
                text_y += 22
            if passive_r5 != passive_r1:
                values = []
                for data in ((weapon.get("affixData") or {}).get("datas") or {}).values():
                    if data:
                        values.append(str(data[-1]))
                base.draw_text_ellipsis(canvas, f"精炼5数值：{' / '.join(values)}", M + 14, y + box_h - 12, width - M * 2 - 28, base.font(13), base.color(base.GOLD))
            y += box_h + 14

        materials = weapon.get("materials") or {}
        y = _section_title(canvas, "突破材料", y)
        material_text = "　".join(str(value) for value in materials.values() if value)
        base.draw_rounded(canvas, M, y, width - M * 2, 66, 12, base.color("#68000000"))
        _draw_wrapped(canvas, material_text, M + 14, y + 26, width - M * 2 - 28, base.font(14), base.color(base.TEXT_MAIN), 21)
        return int(y + 66 + M)

    return base.render_card(W, builder)


async def render_encyclopedia_index(kind: str) -> bytes:
    """渲染角色或武器的可查询名称索引。"""
    if kind == "character":
        items = meta.list_characters("gs")
        grouped = defaultdict(list)
        for item in items:
            grouped[str(item.get("elem") or "multi")].append(item)
        groups = [(_ELEM_TITLE.get(key, key), grouped[key]) for key in _ELEM_ORDER if grouped.get(key)]
        title = "原神角色图鉴"
    elif kind == "weapon":
        items = meta.list_weapons("gs")
        grouped = defaultdict(list)
        for item in items:
            grouped[str(item.get("type") or "other")].append(item)
        order = ["sword", "claymore", "polearm", "bow", "catalyst"]
        groups = [(_WEAPON_TYPE.get(key, key), grouped[key]) for key in order if grouped.get(key)]
        title = "原神武器图鉴"
    else:
        raise ValueError(f"未知图鉴类型: {kind}")

    columns, col_width, row_height = 4, 196, 31

    def builder(canvas: skia.Canvas, width: int, height: int) -> int:
        if height:
            base.draw_gradient(canvas, 0, 0, width, height, [base.color("#17244d"), base.color("#5a4623")])
        y = float(M)
        base.draw_text(canvas, title, M, y + 36, base.font(32, "title"), base.color(base.TEXT_MAIN))
        base.draw_text(canvas, f"共 {len(items)} 项 · 发送 /名称图鉴 查看详情", M, y + 64, base.font(14), base.color(base.TEXT_SUB))
        y += 86
        for group_title, group_items in groups:
            base.draw_text(canvas, group_title, M, y + 25, base.font(20, "title"), base.color(base.GOLD))
            y += 37
            rows = (len(group_items) + columns - 1) // columns
            for index, item in enumerate(group_items):
                col, row = index % columns, index // columns
                x, yy = M + col * (col_width + 8), y + row * row_height
                base.draw_rounded(canvas, x, yy, col_width, row_height - 5, 6, base.color("#58000000"))
                label = f"{item.get('star') or '?'}★ {item.get('name') or '未知'}"
                base.draw_text_ellipsis(canvas, label, x + 8, yy + 19, col_width - 16, base.font(13), base.color(base.TEXT_MAIN))
            y += rows * row_height + 13
        return int(y + M)

    return base.render_card(W, builder)
