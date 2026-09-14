# ruff: noqa: E501
"""原神角色与武器图鉴卡片。"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from html import unescape
from typing import Any

import skia

from ..core import meta
from ..core.artis_mark import key_title
from ..core.attr_calc import elem_name
from . import base

W, M = 860, 24


async def _material_images(materials: dict) -> list:
    entries = [(kind, str(name)) for kind, name in materials.items() if name]
    images = await asyncio.gather(*(
        base.fetch_image(f"meta-gs/material/{kind}/{name}.webp") for kind, name in entries
    ))
    return [(name, image) for (_, name), image in zip(entries, images)]


def _draw_materials(canvas, cards: list, y: float, width: int) -> float:
    columns = 3
    cell_width = (width - M * 2 - 20) / columns
    for index, (name, image) in enumerate(cards):
        x = M + (index % columns) * (cell_width + 10)
        yy = y + (index // columns) * 124
        base.draw_rounded(canvas, x, yy, cell_width, 114, 10, base.color("#68000000"))
        if image is not None:
            base.draw_image_contain(canvas, image, x + (cell_width - 68) / 2, yy + 8, 68, 68)
        else:
            base.draw_image_or_placeholder(canvas, None, (x + (cell_width - 68) / 2, yy + 8, 68, 68), name)
        base.draw_text_center(canvas, name, x + cell_width / 2, yy + 98, base.font(13), base.color(base.TEXT_MAIN))
    return y + ((len(cards) + columns - 1) // columns) * 124 + 4

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
        value = "\n".join(str(item) for item in value if item)
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:h[1-6]|p|div|li)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in unescape(text).splitlines()]
    return "\n".join(line for line in lines if line)


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


def talent_image_path(character: meta.CharacterMeta, key: str) -> str:
    """遵循上游 CharImg：普攻按武器类型，战技/爆发复用技能升级命座图标。"""
    if key == "a":
        return f"common/item/atk-{character.get('weapon') or 'sword'}.webp"
    constellation = (character.get("talentCons") or {}).get(key)
    if constellation:
        return meta.char_img(character.name, f"cons{constellation}", "gs")
    return meta.char_img(character.name, f"talent-{key}", "gs")


async def render_character_encyclopedia(character: meta.CharacterMeta) -> bytes:
    """渲染原神角色图鉴，无需玩家面板数据。"""
    name = character.name
    splash = await base.fetch_image(meta.char_img(name, "splash", "gs"))
    talents = character.get("talent") or {}
    talent_rows = [(key, talents.get(key) or {}) for key in ("a", "e", "q")]
    stats = character_level_stats(character)
    material_cards = await _material_images(character.get("materials") or {})
    passives = character.get("passive") or []
    constellations = character.get("cons") or {}
    paths = {f"talent-{key}": talent_image_path(character, key) for key, talent in talent_rows if talent}
    paths.update({f"passive-{i}": meta.char_img(name, f"passive{i}", "gs") for i, item in enumerate(passives) if item})
    paths.update({f"cons-{i}": meta.char_img(name, f"cons{i}", "gs") for i in range(1, 7) if constellations.get(str(i))})
    # 技能与命座复用同一图标，去重后并发下载。
    unique_paths = list(dict.fromkeys(paths.values()))
    downloaded = await asyncio.gather(*(base.fetch_image(path) for path in unique_paths))
    images_by_path = dict(zip(unique_paths, downloaded))
    icons = {key: images_by_path[path] for key, path in paths.items()}

    def draw_icon(canvas, image, y, label):
        rect = (M + 8, y + 7, 48, 48)
        if image is not None:
            base.draw_image_contain(canvas, image, *rect)
        else:
            base.draw_image_or_placeholder(canvas, None, rect, label, 8)

    def draw_detail_card(canvas, y, image, fallback, label, title, description):
        description = _clean_text(description)
        description_font = base.font(14)
        lines = _wrap_text(description, description_font, W - M * 2 - 28) if description else []
        card_height = 72 if not lines else 80 + len(lines) * 21
        base.draw_rounded(canvas, M, y, W - M * 2, card_height, 10, base.color("#68000000"))
        draw_icon(canvas, image, y, fallback)
        base.draw_text(canvas, label, M + 70, y + 24, base.font(13), base.color(base.NUM_GOLD))
        base.draw_text_ellipsis(canvas, title or "-", M + 70, y + 50, W - M * 2 - 86, base.font(17), base.color(base.TEXT_MAIN))
        if lines:
            text_y = y + 82
            for line in lines:
                base.draw_text(canvas, line, M + 14, text_y, description_font, base.color(base.TEXT_MAIN))
                text_y += 21
        return y + card_height + 8

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

        y = _section_title(canvas, "培养材料", y)
        y = _draw_materials(canvas, material_cards, y, width)

        y = _section_title(canvas, "战斗天赋", y)
        for key, talent in talent_rows:
            if not talent:
                continue
            label = {"a": "普通攻击", "e": "元素战技", "q": "元素爆发"}[key]
            y = draw_detail_card(
                canvas,
                y,
                icons.get(f"talent-{key}"),
                key.upper(),
                label,
                talent.get("name") or "-",
                talent.get("desc"),
            )

        if passives:
            y = _section_title(canvas, "固有天赋", y + 2)
            for index, item in enumerate(passives):
                if not item:
                    continue
                y = draw_detail_card(
                    canvas,
                    y,
                    icons.get(f"passive-{index}"),
                    item.get("name") or "天赋",
                    f"固有天赋 {index + 1}",
                    item.get("name") or "-",
                    item.get("desc"),
                )

        constellations = character.get("cons") or {}
        if constellations:
            y = _section_title(canvas, "命之座", y + 2)
            for number in ("1", "2", "3", "4", "5", "6"):
                item = constellations.get(number) or {}
                if not item:
                    continue
                y = draw_detail_card(
                    canvas,
                    y,
                    icons.get(f"cons-{number}"),
                    number,
                    f"{number}命",
                    item.get("name") or "-",
                    item.get("desc"),
                )
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
    material_cards = await _material_images(weapon.get("materials") or {})

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

        y = _section_title(canvas, "突破材料", y)
        y = _draw_materials(canvas, material_cards, y, width)
        return int(y + M)

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
