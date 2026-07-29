# ruff: noqa: E501, E701, E702
"""按角色归类的圣遗物/遗器列表卡片。"""
from __future__ import annotations

from ..core.artis_mark import calc_mark
from ..core.attr_calc import calc_attr
from ..core.player import Player
from . import base


async def render_artis_list(player: Player, game: str) -> bytes:
    rows = []
    for avatar in player.avatars.values():
        try:
            mark = calc_mark(avatar, calc_attr(avatar, game), game)
        except Exception:
            mark = {"mark": 0, "markClass": None, "artis": {}}
        rows.append((avatar, mark))

    def builder(canvas, w: int, h: int) -> int:
        if h: base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient('sr' if game == 'sr' else 'geo'))
        y = 18.; base.draw_text(canvas, '遗器列表' if game == 'sr' else '圣遗物列表', 18, y + 30, base.font(28, 'title'), base.color(base.TEXT_MAIN)); y += 50
        for avatar, mark in rows:
            base.draw_rounded(canvas, 18, y, w - 36, 34, 8, base.color('#a0000000'))
            base.draw_text(canvas, f"{avatar.get('name')}  评分 {mark['mark']:.1f} {mark.get('markClass') or '-'}", 30, y + 23, base.font(16), base.color(base.NUM_GOLD)); y += 40
            for piece in mark['artis'].values():
                base.draw_text_ellipsis(canvas, f"{piece['name']} +{piece['level']}  {piece.get('set') or ''}  {piece['mark']:.1f}", 34, y + 18, w - 68, base.font(13), base.color(base.TEXT_MAIN)); y += 23
            y += 7
        return int(y + 16)

    return base.render_card(760, builder)
