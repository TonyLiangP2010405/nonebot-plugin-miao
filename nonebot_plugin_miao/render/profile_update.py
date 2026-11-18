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


def update_items(
    player: Any, game: str, new_chars: list[str]
) -> list[tuple[str, dict[str, Any], str, bool]]:
    """更新回执条目：本地全部角色，本次接口返回的角色排前面并标记。"""
    updated_names = {str(name) for name in new_chars}
    items: list[tuple[str, dict[str, Any], str, bool]] = []
    for avatar in (getattr(player, "avatars", {}) or {}).values():
        char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
        name = char.name if char else str(avatar.get("name") or avatar.get("id") or "未知角色")
        rel = meta.char_img(name, "face", game)
        items.append((name, avatar, rel, name in updated_names))
    # 本次更新的角色优先展示，其余角色仍然列出
    items.sort(key=lambda item: 0 if item[3] else 1)
    return items


async def render_profile_update(
    player: Any,
    game: str,
    new_chars: list[str],
    uid: str,
    source: str = "",
) -> bytes:
    """渲染更新回执：展示本地全部角色，并标出本次接口返回的角色。

    更新接口（尤其星铁支援/展示柜）可能只返回部分角色，直接按 ``new_chars``
    出图会让人误以为角色丢了；这里以 ``player.avatars`` 全量为准。
    """
    items = update_items(player, game, new_chars)
    faces = await asyncio.gather(*(base.fetch_image(rel) for _, _, rel, _ in items))
    game_name = "星铁" if game == "sr" else "原神"
    elem = (items[0][1].get("elem") if items else "") or ("sr" if game == "sr" else "hydro")
    updated_count = sum(1 for item in items if item[3])

    def builder(canvas, w: int, h: int) -> int:
        if h:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient(elem))
        base.draw_text(canvas, "面板更新完成", M, 52, base.font(30, "title"), base.color(base.TEXT_MAIN))
        sub = f"{game_name} · UID {uid} · {_fmt_time(getattr(player, 'update_time', 0))}"
        if source:
            sub += f" · {source}"
        base.draw_text(canvas, sub, M, 78, base.font(13), base.color(base.TEXT_SUB))
        total = len(items)
        if updated_count:
            summary = f"本次接口返回 {updated_count} 个角色，本地共 {total} 个角色（含已有数据）"
        else:
            summary = f"本次没有新角色，本地共 {total} 个角色"
        base.draw_text(canvas, summary, M, 104, base.font(14), base.color(base.NUM_GOLD))
        y = 124.0
        if not items:
            base.draw_rounded(canvas, M, y, w - M * 2, 84, 12, base.color("#CC1A1A22"))
            base.draw_text(
                canvas,
                "本地暂无角色数据",
                M + 22,
                y + 38,
                base.font(20),
                base.color(base.TEXT_MAIN),
            )
            base.draw_text(
                canvas,
                "发送 /更新面板 拉取展示柜，或 /面板列表 查看现有角色",
                M + 22,
                y + 64,
                base.font(12),
                base.color(base.TEXT_SUB),
            )
            return int(y + 108)
        cols = 4
        for i, ((name, avatar, _, updated), image) in enumerate(zip(items, faces)):
            x = M + (i % cols) * (COL_W + 10)
            yy = y + (i // cols) * (CARD_H + 10)
            base.draw_shadow(canvas, base.rrect(x, yy, COL_W, CARD_H, 10), blur=8)
            if updated:
                base.draw_rounded(
                    canvas, x - 1, yy - 1, COL_W + 2, CARD_H + 2, 11, base.color(base.NUM_GOLD)
                )
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
                yy + 124,
                base.font(12),
                base.color(base.TEXT_SUB),
            )
            base.draw_text(
                canvas,
                "本次更新" if updated else "已有数据",
                x + 12,
                yy + 143,
                base.font(10),
                base.color(base.NUM_GOLD if updated else "#8a94a6"),
            )
        rows = max(1, (len(items) + cols - 1) // cols)
        return int(y + rows * (CARD_H + 10) + 12)

    return base.render_card(W, builder)
