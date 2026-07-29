# ruff: noqa: E501, E701, E702
"""角色详细面板卡片。"""
from __future__ import annotations

from typing import Any

from ..core import meta
from ..core.artis_mark import format_value, key_title
from ..core.attr_calc import elem_name
from . import base

W, M = 820, 18


def _stat(attr: dict[str, Any], key: str) -> str:
    return f"{key_title(key, 'gs') if key not in ('hp', 'atk', 'def') else {'hp':'生命','atk':'攻击','def':'防御'}[key]} {attr.get(key, 0):.0f}"


async def render_profile_detail(avatar: dict, attr: dict, mark: dict, game: str, dmg: dict | None = None) -> bytes:
    """渲染属性、武器、圣遗物与可选伤害结果。"""
    char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
    name = char.name if char else avatar.get("name") or "未知"
    face = await base.fetch_image(meta.char_img(name, "splash", game))
    weapon = avatar.get("weapon") or {}
    weapon_img = await base.fetch_image(meta.weapon_img(weapon["name"], "icon", game)) if weapon.get("name") else None

    def builder(canvas, w: int, h: int) -> int:
        if h:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient(avatar.get("elem")))
        y = float(M)
        base.draw_text(canvas, name, M, y + 34, base.font(32, "title"), base.color(base.TEXT_MAIN))
        sub = f"Lv{avatar.get('level') or '?'} · C{avatar.get('cons') or 0} · {elem_name(avatar.get('elem'), game)}"
        base.draw_text(canvas, sub, M, y + 58, base.font(15), base.color(base.GOLD))
        base.draw_text(canvas, f"评分 {mark['mark']:.1f}  {mark.get('markClass') or '-'}", w - 210, y + 38, base.font(17), base.color(base.NUM_GOLD))
        y += 76
        base.draw_image_or_placeholder(canvas, face, (M, y, 265, 250), name, 12)
        x, inner = M + 280, w - M - (M + 280)
        base.draw_rounded(canvas, x, y, inner, 112, 10, base.color("#99000000"))
        keys = ('hp', 'atk', 'def', 'speed') if game == 'sr' else ('hp', 'atk', 'def', 'mastery')
        for i, key in enumerate(keys):
            yy = y + 30 + (i // 2) * 48
            xx = x + 16 + (i % 2) * (inner / 2)
            label = {'hp':'生命','atk':'攻击','def':'防御','speed':'速度','mastery':'精通'}[key]
            base.draw_text(canvas, f"{label} {attr.get(key, 0):.0f}", xx, yy, base.font(17), base.color(base.TEXT_MAIN))
        for i, key in enumerate(('cpct', 'cdmg', 'recharge', 'dmg')):
            yy = y + 132 + i * 25
            label = {'cpct':'暴击率','cdmg':'暴击伤害','recharge':'元素充能','dmg':'伤害加成'}[key]
            base.draw_text(canvas, f"{label} {attr.get(key, 0):.1f}%", x + 16, yy, base.font(14), base.color(base.TEXT_SUB))
        if weapon.get('name'):
            base.draw_rounded(canvas, x, y + 235, inner, 46, 8, base.color("#88000000"))
            base.draw_image_or_placeholder(canvas, weapon_img, (x + 5, y + 240, 36, 36), weapon['name'], 5)
            base.draw_text_ellipsis(canvas, f"{weapon['name']}  Lv{weapon.get('level') or 1}  {'叠' if game == 'sr' else '精'}{weapon.get('affix') or 1}", x + 50, y + 264, inner - 60, base.font(14), base.color(base.TEXT_MAIN))
        y += 302
        base.draw_text(canvas, '圣遗物' if game == 'gs' else '遗器', M, y + 22, base.font(20, 'title'), base.color(base.TEXT_MAIN))
        y += 32
        for idx, piece in mark['artis'].items():
            base.draw_rounded(canvas, M, y, w - 2 * M, 38, 7, base.color('#80000000'))
            main = piece.get('main') or {}; main_txt = f"{key_title(main.get('key'), game)} {format_value(main.get('key'), main.get('value') or 0, game)}" if main else ''
            base.draw_text_ellipsis(canvas, f"{piece['name']} +{piece['level']}  {main_txt}", M + 10, y + 25, w - 240, base.font(14), base.color(base.TEXT_MAIN))
            base.draw_text(canvas, f"{piece['mark']:.1f} {piece.get('markClass') or '-'}", w - 130, y + 25, base.font(14), base.color(base.NUM_GOLD))
            y += 43
        if dmg:
            y += 4; base.draw_text(canvas, '伤害计算', M, y + 22, base.font(20, 'title'), base.color(base.TEXT_MAIN)); y += 32
            for row in dmg.get('dmgData') or []:
                base.draw_text_ellipsis(canvas, f"{row['title']}：期望 {row['avg']:.0f} / 暴击 {row['dmg']:.0f}", M + 8, y + 20, w - 2 * M - 16, base.font(14), base.color(base.TEXT_MAIN)); y += 27
        return int(y + 20)

    return base.render_card(W, builder)
