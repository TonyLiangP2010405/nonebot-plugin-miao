"""十连模拟抽卡结果卡片（对照 refs/Yunzai-genshin resources/html/gacha/gacha-trial.html）

布局对应关系（HTML -> 手绘）：
- 原版 1286x670 横向长条 10 卡 -> 2×5 网格物品卡（图片/星级色边框/名字/角标）
- info-name（玩家名）+ info-count（info 累计信息）-> 底部信息行 + 发起者
- poor-info/poor-bing（定轨武器/命定值）-> 头部右侧定轨信息
- item-star / item-element 角标 -> 星星排 + 元素渐变底（无图时）；isBigUP/isBing/have 文字角标
"""
from __future__ import annotations

from datetime import datetime

import skia

from . import base

W = 800
COLS = 5
CARD_W, CARD_H = 140, 190
GAP = 12


async def render_gacha_trial(result: dict, sender_name: str) -> bytes:
    """渲染十连结果卡片，返回 PNG bytes

    result: do_gacha 的输出（{list, info, nowFive, nowFour, poolName, isWeapon, bingWeapon, lifeNum}）
    """
    items = result.get("list") or []
    info = result.get("info") or ""
    pool_name = result.get("poolName") or ""
    is_weapon = bool(result.get("isWeapon"))
    bing_weapon = result.get("bingWeapon")
    life_num = result.get("lifeNum", 0)

    images: list[skia.Image | None] = [await base.fetch_image(it.get("imgFile", "")) for it in items]

    grid_w = COLS * CARD_W + (COLS - 1) * GAP
    gx0 = (W - grid_w) / 2

    def builder(canvas: skia.Canvas, w: int, h: int) -> int:
        if h > 0:
            base.draw_gradient(canvas, 0, 0, w, h, base.elem_gradient("hydro"))
        y = 18.0

        # ---------------- 头部（对照 info-name / poor-info / poor-bing） ----------------
        base.draw_text(canvas, "十连抽卡", 26, y + 38, base.font(34, "title"), base.color(base.TEXT_MAIN))
        rf = base.font(16)
        right_txt = pool_name
        base.draw_text(canvas, right_txt, w - 26 - base.measure(right_txt, rf), y + 30, rf, base.color(base.GOLD))
        if is_weapon and bing_weapon:
            bing_txt = f"定轨：{bing_weapon} · 命定值：{life_num}"
            base.draw_text(canvas, bing_txt, w - 26 - base.measure(bing_txt, rf), y + 56, rf,
                           base.color(base.NUM_GOLD))
        base.draw_text(canvas, sender_name, 26, y + 66, base.font(15), base.color(base.TEXT_SUB))
        y += 84

        # ---------------- 2×5 物品卡网格 ----------------
        for i, it in enumerate(items):
            cx = gx0 + (i % COLS) * (CARD_W + GAP)
            cy = y + (i // COLS) * (CARD_H + GAP)
            star = it.get("star", 3)
            border_c = base.color(base.STAR_COLORS.get(star, base.STAR_COLORS[3]))
            # 星级色边框（对照 item-shadow 的星级光影）
            base.draw_shadow(canvas, base.rrect(cx, cy, CARD_W, CARD_H, 10), blur=10, dy=3)
            base.draw_rounded(canvas, cx, cy, CARD_W, CARD_H, 10, border_c)
            ix, iy, iw, ih = cx + 4, cy + 4, CARD_W - 8, CARD_H - 8
            img = images[i] if i < len(images) else None
            # 元素色渐变底常驻（对照 item-bg 元素卡背），图片 contain 完整显示不裁剪
            base.draw_gradient(canvas, ix, iy, iw, ih, base.elem_gradient(it.get("element")), r=8)
            if img is not None:
                base.draw_image_contain(canvas, img, ix, iy, iw, ih, r=8)
            else:
                ef = base.font(18)
                base.draw_text_center(canvas, it.get("element", ""), ix + iw / 2,
                                      iy + ih / 2 - 16, ef, base.color("#ccffffff"))
            # 底部名字条
            name_h = 30
            base.draw_rounded(canvas, ix, iy + ih - name_h, iw, name_h, 8, base.color("#b3000000"))
            nf = base.font(14)
            base.draw_text_center(canvas, base.ellipsize(it.get("name", ""), nf, iw - 10), ix + iw / 2,
                                  base.baseline_center(iy + ih - name_h, name_h, nf), nf,
                                  base.color(base.TEXT_MAIN))
            # 星星排（对照 item-star）
            base.draw_stars(canvas, ix + iw / 2, iy + ih - name_h - 14, star, r=7)
            # 角标（左上竖排：大保底/定轨/已拥有；右上：N 抽）
            bx, by = ix + 6, iy + 6
            badges = [
                b
                for b in (
                    ("大保底", "#ffeb73", "#6f4b00") if it.get("isBigUP") else None,
                    ("定轨", "#2e6fbd", "#ffffff") if it.get("isBing") else None,
                    ("已拥有", "#555555", "#dddddd") if it.get("have") else None,
                )
                if b is not None
            ]
            for text, bg, fg in badges:
                bf = base.font(11)
                bw = base.measure(text, bf) + 12
                base.draw_rounded(canvas, bx, by, bw, 20, 10, base.color(bg))
                base.draw_text_center(canvas, text, bx + bw / 2, base.baseline_center(by, 20, bf), bf,
                                      base.color(fg))
                by += 24
            if star == 5 and it.get("num") and not it.get("have"):
                num_txt = f"「{it['num']}抽」"
                nf2 = base.num_font(it["num"], 13)
                tw = base.measure(num_txt, nf2) + 12
                base.draw_rounded(canvas, ix + iw - tw - 6, iy + 6, tw, 20, 10, base.color("#b3000000"))
                base.draw_text_center(canvas, num_txt, ix + iw - tw / 2 - 6,
                                      base.baseline_center(iy + 6, 20, nf2), nf2, base.color(base.NUM_GOLD))
        rows = (len(items) + COLS - 1) // COLS
        y += rows * CARD_H + max(0, rows - 1) * GAP + 16

        # ---------------- 底部 info 行（对照 info-count） ----------------
        info_f = base.font(17)
        base.draw_rounded(canvas, 26, y, w - 52, 40, 8, base.color("#73000000"))
        base.draw_text_center(canvas, f"{info} · {sender_name}", w / 2, base.baseline_center(y, 40, info_f),
                              info_f, base.color(base.TEXT_MAIN))
        y += 40 + 10
        base.draw_text_center(canvas, f"生成于 {datetime.now():%Y-%m-%d %H:%M} · nonebot-plugin-miao",
                              w / 2, y + 12, base.font(12), base.color("#99ffffff"))
        y += 32
        return int(y)

    return base.render_card(W, builder)
