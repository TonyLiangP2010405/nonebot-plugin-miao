# ruff: noqa: E501
"""群面板排名卡片：群友头像、角色头像、评分/伤害与名次。"""
from __future__ import annotations

import asyncio

from ..core import meta
from ..core.profile_rank import MODE_DMG, MODE_MARK, MODE_NAME, RankEntry
from . import base
from .profile_detail import _CLASS_COLOR

W, M = 860, 22
ROW_H, GAP = 86, 8
_RANK_COLORS = {1: "#ffd166", 2: "#c9d6e8", 3: "#d99a6c"}


def _qq_avatar_url(qq: str) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100"


async def render_profile_rank(
    entries: list[RankEntry],
    game: str,
    *,
    char_name: str | None = None,
    mode: str = MODE_MARK,
    group_label: str = "",
    member_count: int = 0,
) -> bytes:
    """渲染群排名；entries 为空时输出引导卡片。"""
    faces = await asyncio.gather(
        *(base.fetch_image(meta.char_img(e.char_name, "face", game)) for e in entries)
    )
    avatars = await asyncio.gather(*(base.fetch_image(_qq_avatar_url(e.qq)) for e in entries))
    mode_name = MODE_NAME.get(mode, MODE_NAME[MODE_MARK])
    title = f"{char_name} · 群面板排名" if char_name else "群友面板排名"
    elem = (entries[0].avatar.get("elem") if entries else "") or ("sr" if game == "sr" else "hydro")
    game_name = "星铁" if game == "sr" else "原神"

    def builder(canvas, w: int, h: int) -> int:
        if h:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient(elem))
        base.draw_text(canvas, title, M, 48, base.font(30, "title"), base.color(base.TEXT_MAIN))
        sub = f"{game_name} · {group_label or '本群'} · 按{mode_name}排序"
        if member_count:
            sub += f" · 共 {member_count} 位群友参与"
        base.draw_text(canvas, sub, M, 74, base.font(13), base.color(base.TEXT_SUB))
        base.draw_text(
            canvas,
            "数据来自群友 /更新面板；发送 /角色名面板排名 可查看指定角色",
            M,
            94,
            base.font(12),
            base.color(base.TEXT_SUB),
        )
        y = 112.0
        if not entries:
            base.draw_rounded(canvas, M, y, w - M * 2, 120, 12, base.color("#CC1A1A22"))
            base.draw_text(
                canvas,
                "本群暂时没有可排名的面板数据",
                M + 24,
                y + 46,
                base.font(22),
                base.color(base.TEXT_MAIN),
            )
            base.draw_text(
                canvas,
                "请群友先发送 /绑定uid <UID> 和 /更新面板，再发送 /面板排名",
                M + 24,
                y + 78,
                base.font(13),
                base.color(base.TEXT_SUB),
            )
            return int(y + 144)

        # 表头
        base.draw_text(canvas, "排名", M + 6, y + 12, base.font(12), base.color(base.TEXT_SUB))
        base.draw_text(canvas, "群友 / 角色", M + 76, y + 12, base.font(12), base.color(base.TEXT_SUB))
        base.draw_text(canvas, mode_name, w - M - 132, y + 12, base.font(12), base.color(base.TEXT_SUB))
        y += 22
        for index, (entry, face, avatar) in enumerate(zip(entries, faces, avatars), 1):
            yy = y + (index - 1) * (ROW_H + GAP)
            rank_color = _RANK_COLORS.get(index, "#8a94a6")
            base.draw_shadow(canvas, base.rrect(M, yy, w - M * 2, ROW_H, 10), blur=7)
            bg = "#E61A202C" if index % 2 else "#E6141822"
            base.draw_rounded(canvas, M, yy, w - M * 2, ROW_H, 10, base.color(bg))
            if index <= 3:
                base.draw_rounded(
                    canvas, M, yy, 4, ROW_H, 2, base.color(rank_color)
                )
            # 名次
            base.draw_rounded(canvas, M + 12, yy + 24, 38, 38, 19, base.color(f"#33{rank_color[1:]}"))
            base.draw_text_center(
                canvas, str(index), M + 31, yy + 50, base.font(20, "number"), base.color(rank_color)
            )
            # QQ 头像 + 角色头像
            base.draw_image_or_placeholder(
                canvas, avatar, (M + 62, yy + 15, 56, 56), entry.nickname, 28
            )
            base.draw_image_or_placeholder(
                canvas, face, (M + 128, yy + 15, 56, 56), entry.char_name, 10
            )
            # 昵称 / 角色信息
            base.draw_text_ellipsis(
                canvas,
                entry.nickname,
                M + 198,
                yy + 32,
                260,
                base.font(18),
                base.color(base.TEXT_MAIN),
            )
            detail = (
                f"UID {entry.uid} · {entry.char_name} Lv{entry.level} C{entry.cons}"
                f" · {entry.elem or '—'}"
            )
            base.draw_text_ellipsis(
                canvas, detail, M + 198, yy + 58, 330, base.font(12), base.color(base.TEXT_SUB)
            )
            # 分数
            if mode == MODE_DMG and entry.dmg_avg is not None:
                score_text = f"{entry.dmg_avg:,.0f}"
                score_sub = f"评分 {entry.mark:.1f}"
            else:
                score_text = f"{entry.score:.1f}"
                score_sub = f"评分 {entry.mark:.1f}"
            base.draw_text_center(
                canvas, score_text, w - M - 55, yy + 42, base.font(26, "number"), base.color(base.NUM_GOLD)
            )
            base.draw_text_center(
                canvas, score_sub, w - M - 55, yy + 66, base.font(11), base.color(base.TEXT_SUB)
            )
            cls = entry.mark_class
            if cls:
                badge_w = 54
                bx = w - M - 172
                base.draw_rounded(
                    canvas,
                    bx,
                    yy + 30,
                    badge_w,
                    26,
                    6,
                    base.color(f"#33{_CLASS_COLOR.get(cls, '#8a94a6')[1:]}"),
                )
                base.draw_text_center(
                    canvas,
                    cls,
                    bx + badge_w / 2,
                    yy + 49,
                    base.font(14),
                    base.color(_CLASS_COLOR.get(cls, base.TEXT_SUB)),
                )
        rows = len(entries)
        return int(y + rows * (ROW_H + GAP) + 8)

    return base.render_card(W, builder)
