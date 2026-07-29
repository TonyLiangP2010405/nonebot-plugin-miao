# ruff: noqa: E501
"""角色面板列表卡片。"""
from __future__ import annotations

from ..core import meta
from ..core.artis_mark import calc_mark
from ..core.attr_calc import calc_attr
from ..core.player import Player
from . import base

W, M, COL_W, CARD_H = 760, 18, 174, 152


async def render_profile_list(player: Player, game: str) -> bytes:
    """渲染玩家角色网格，缺失头像时安全回退到文字占位。"""
    cards = []
    for avatar in player.avatars.values():
        char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
        name = char.name if char else (avatar.get("name") or "未知")
        try:
            mark = calc_mark(avatar, calc_attr(avatar, game), game)
        except Exception:
            # 元数据偶有缺少评分规则；列表不应因单个角色而无法显示。
            mark = {"mark": 0, "markClass": None}
        cards.append((avatar, name, mark, await base.fetch_image(meta.char_img(name, "face", game))))
    cols = 4

    def builder(canvas, w: int, h: int) -> int:
        if h:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient("sr" if game == "sr" else "hydro"))
        y = float(M)
        title = f"{player.name or '旅行者'} 的{'星铁' if game == 'sr' else '原神'}面板"
        base.draw_text(canvas, title, M, y + 30, base.font(27, "title"), base.color(base.TEXT_MAIN))
        sub = f"UID {player.uid} · {player.data_source or '本地'} · {len(cards)} 名角色"
        base.draw_text(canvas, sub, M, y + 52, base.font(13), base.color(base.TEXT_SUB))
        y += 70
        for i, (avatar, name, mark, image) in enumerate(cards):
            x = M + (i % cols) * (COL_W + 10)
            yy = y + (i // cols) * (CARD_H + 10)
            base.draw_shadow(canvas, base.rrect(x, yy, COL_W, CARD_H, 10), blur=8)
            base.draw_rounded(canvas, x, yy, COL_W, CARD_H, 10, base.color("#99000000"))
            base.draw_image_or_placeholder(canvas, image, (x + 10, yy + 10, COL_W - 20, 76), name)
            base.draw_text_ellipsis(canvas, name, x + 10, yy + 108, COL_W - 20, base.font(18), base.color(base.TEXT_MAIN))
            base.draw_text(canvas, f"Lv{avatar.get('level') or '?'}  C{avatar.get('cons') or 0}", x + 10, yy + 130, base.font(13), base.color(base.TEXT_SUB))
            score = f"评分 {mark['mark']:.1f}  {mark.get('markClass') or '-'}"
            base.draw_text(canvas, score, x + 10, yy + 148, base.font(13), base.color(base.NUM_GOLD))
        rows = max(1, (len(cards) + cols - 1) // cols)
        return int(y + rows * (CARD_H + 10) + 18)

    return base.render_card(W, builder)
