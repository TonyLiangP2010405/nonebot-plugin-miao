# ruff: noqa: E501
"""面板更新结果卡片：更新成功后的图片回执（替代纯文字列表）。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core import meta
from ..core.attr_calc import elem_name
from . import base

W, M = 760, 20
COL_W, CARD_H = 172, 150


def _fmt_time(stamp: int) -> str:
    if not stamp:
        stamp = int(time.time())
    return time.strftime("%m-%d %H:%M", time.localtime(stamp))


async def render_profile_update(
    player: Any,
    game: str,
    new_chars: list[str],
    uid: str,
    source: str = "",
) -> bytes:
    """渲染「本次更新了哪些角色」的图片回执。"""
    items: list[tuple[str, dict[str, Any], str]] = []
    for name in new_chars:
        avatar = player.get_avatar(name) or {}
        char = meta.get_character(avatar.get("id") or name, game)
        real_name = char.name if char else name
        items.append((real_name, avatar, meta.char_img(real_name, "face", game)))
    faces = await asyncio.gather(*(base.fetch_image(rel) for _, _, rel in items))
    game_name = "星铁" if game == "sr" else "原神"
    elem = (items[0][1].get("elem") if items else "") or ("sr" if game == "sr" else "hydro")

    def builder(canvas, w: int, h: int) -> int:
        if h:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient(elem))
        base.draw_text(canvas, "面板更新完成", M, 52, base.font(30, "title"), base.color(base.TEXT_MAIN))
        sub = f"{game_name} · UID {uid} · {_fmt_time(getattr(player, 'update_time', 0))}"
        if source:
            sub += f" · {source}"
        base.draw_text(canvas, sub, M, 78, base.font(13), base.color(base.TEXT_SUB))
        total = len(getattr(player, "avatars", {}) or {})
        base.draw_text(
            canvas,
            f"本次新增/更新 {len(items)} 个角色，当前共 {total} 个角色",
            M,
            104,
            base.font(14),
            base.color(base.NUM_GOLD),
        )
        y = 124.0
        if not items:
            base.draw_rounded(canvas, M, y, w - M * 2, 84, 12, base.color("#CC1A1A22"))
            base.draw_text(
                canvas,
                "本次没有新角色，面板数据已刷新",
                M + 22,
                y + 38,
                base.font(20),
                base.color(base.TEXT_MAIN),
            )
            base.draw_text(
                canvas,
                "发送 /面板列表 查看全部角色，或 /角色名面板 查看详情",
                M + 22,
                y + 64,
                base.font(12),
                base.color(base.TEXT_SUB),
            )
            return int(y + 108)
        cols = 4
        for i, ((name, avatar, _), image) in enumerate(zip(items, faces)):
            x = M + (i % cols) * (COL_W + 10)
            yy = y + (i // cols) * (CARD_H + 10)
            base.draw_shadow(canvas, base.rrect(x, yy, COL_W, CARD_H, 10), blur=8)
            base.draw_rounded(canvas, x, yy, COL_W, CARD_H, 10, base.color("#E6121722"))
            base.draw_image_or_placeholder(
                canvas, image, (x + 12, yy + 10, COL_W - 24, 72), name
            )
            base.draw_text_ellipsis(
                canvas, name, x + 12, yy + 100, COL_W - 24, base.font(18), base.color(base.TEXT_MAIN)
            )
            elem_text = elem_name(avatar.get("elem"), game) or "—"
            base.draw_text(
                canvas,
                f"Lv{avatar.get('level') or '?'}  C{avatar.get('cons') or 0}  {elem_text}",
                x + 12,
                yy + 126,
                base.font(12),
                base.color(base.TEXT_SUB),
            )
        rows = max(1, (len(items) + cols - 1) // cols)
        return int(y + rows * (CARD_H + 10) + 12)

    return base.render_card(W, builder)
