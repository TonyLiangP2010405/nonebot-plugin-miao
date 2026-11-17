# ruff: noqa: E501, E701, E702
"""按角色归类的圣遗物/遗器列表卡片（含每件圣遗物的主副词条与评分）。"""
from __future__ import annotations

import asyncio
from typing import Any

from ..core import meta
from ..core.artis_mark import calc_mark
from ..core.attr_calc import calc_attr
from ..core.player import Player
from . import base
from . import profile_detail as pd

W, M = 960, 22


async def render_artis_list(player: Player, game: str) -> bytes:
    rows: list[dict[str, Any]] = []
    paths: list[str] = []

    def add(path: str) -> None:
        if path and path not in paths:
            paths.append(path)

    for avatar in player.avatars.values():
        try:
            mark = calc_mark(avatar, calc_attr(avatar, game), game)
        except Exception:
            mark = {"mark": 0, "markClass": None, "artis": {}}
        name = pd._char_name(avatar, game)
        face = meta.char_img(name, "face", game)
        add(face)
        pieces = pd.artis_rows(mark, game)
        for piece in pieces:
            add(piece["img"])
        rows.append({"name": name, "face": face, "mark": mark, "pieces": pieces})

    fetched = await asyncio.gather(*(base.fetch_image(p) for p in paths))
    images = dict(zip(paths, fetched))

    def img(path: str) -> Any:
        return images.get(path)

    title = "遗器列表" if game == "sr" else "圣遗物列表"
    word = "遗器" if game == "sr" else "圣遗物"

    def builder(canvas: Any, w: int, h: int) -> int:
        if h:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient("sr" if game == "sr" else "geo"))
            pd._text(canvas, title, M, 50, 30, base.TEXT_MAIN, "title")
            pd._text(canvas, f"UID {player.uid} · {len(rows)} 名角色 · 评分按{word}词条权重计算", M, 76, 12, base.TEXT_SUB)
        y = 96.0

        for row in rows:
            mark = row["mark"]
            score = float(mark.get("mark") or 0)
            cls = mark.get("markClass")
            if h:
                base.draw_rounded(canvas, M, y, W - 2 * M, 56, 10, base.color("#73000000"))
                base.draw_image_or_placeholder(canvas, img(row["face"]), (M + 10, y + 8, 40, 40), row["name"], 20)
                pd._ellipsis(canvas, row["name"], M + 60, y + 35, 240, 20, base.TEXT_MAIN)
                cls_x = W - M - 16
                pd._text_right(canvas, cls or "-", cls_x, y + 34, 16, pd._CLASS_COLOR.get(cls, base.TEXT_SUB))
                score_text = f"{score:.1f}"
                score_x = cls_x - 26
                pd._text_right(canvas, score_text, score_x, y + 35, 18, base.NUM_GOLD, "number")
                label_x = score_x - base.measure(score_text, base.font(18, "number")) - 8
                pd._text_right(canvas, "评分", label_x, y + 35, 13, base.GOLD)
                sets = " / ".join(f"{k}{v}" for k, v in (mark.get("sets") or {}).items())
                if sets:
                    pd._ellipsis(canvas, sets, M + 300, y + 35, 280, 12, base.TEXT_SUB)
            y += 64

            for piece in row["pieces"]:
                if h:
                    base.draw_rounded(canvas, M + 12, y, W - 2 * M - 24, 74, 8, base.color("#40000000"))
                    base.draw_image_or_placeholder(canvas, img(piece["img"]), (M + 20, y + 12, 50, 50), piece["name"], 8)
                    nx = M + 82
                    nw = min(base.measure(piece["name"], base.font(14)), 180)
                    pd._ellipsis(canvas, piece["name"], nx, y + 28, 190, 14, base.TEXT_MAIN)
                    pd._text(canvas, f"+{piece['level']}", nx + nw + 8, y + 28, 12, base.NUM_GOLD)
                    pd._ellipsis(canvas, piece["set"], nx, y + 48, 200, 11, base.TEXT_SUB)
                    if piece["main"]:
                        pd._text(canvas, piece["main"]["title"], M + 300, y + 30, 11, base.TEXT_SUB)
                        pd._text(canvas, f"+{piece['main']['value']}", M + 300, y + 52, 14, base.TEXT_MAIN)
                    for i, sub in enumerate(piece["subs"][:4]):
                        col, line = i % 2, i // 2
                        x = M + 430 + col * 190
                        yy = y + 30 + line * 22
                        pd._ellipsis(canvas, f"{sub['title']} +{sub['value']}", x, yy, 178, 11, pd._weight_color(sub["weight"]))
                        if sub["upNum"]:
                            vw = base.measure(f"{sub['title']} +{sub['value']}", base.font(11))
                            for d in range(min(sub["upNum"], 5)):
                                base.draw_rounded(canvas, x + vw + 4 + d * 5, yy - 5, 3, 3, 1.5, base.color(pd._weight_color(sub["weight"])))
                    pd._text(canvas, f"{piece['mark']:.1f} {piece['markClass'] or '-'}", W - M - 96, y + 46, 12, pd._CLASS_COLOR.get(piece["markClass"], base.TEXT_SUB))
                y += 82
            y += 10

        return int(y + 14)

    return base.render_card(W, builder)
