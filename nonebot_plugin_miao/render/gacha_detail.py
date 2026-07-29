"""抽卡记录详情卡片（对照 refs/miao-plugin resources/gacha/gacha-detail.html）

布局对应关系（HTML -> 手绘）：
- user-banner（元素背景 + 头像 + 名字/UID + stat 六项）-> 元素渐变整卡背景 + 头部（头像/标题/池名/UID）+ 统计面板
- gacha-list .gacha-item（date + name + icon + process bar）-> 五星条目行（日期/图标/名字/进度条）
- bar 的 gold/good/normal/bad 四档配色照抄 css；UP/歪 用绿/红角标（原版是黄色 UP 标签，按需求改绿/红）
- 无五星占位条目（id=888）显示 "已抽 N 抽"
- 底部追加四星统计摘要 + 生成时间 + 水印
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import skia

from . import base

W = 720
M = 16  # 页边距

# bar 四档（对照 gacha-detail.css .bar.gold/.good/.normal/.bad）：(底色, 字色)
_BAR_TIERS = {
    "gold": ("#ffeb73", "#6f4b00"),
    "good": ("#168b2c", "#ffffff"),
    "normal": ("#6939b7", "#ffffff"),
    "bad": ("#9d3333", "#ffffff"),
}

UP_BADGE = ("UP", "#168b2c", "#ffffff")
WAI_BADGE = ("歪", "#9d3333", "#ffffff")


def _bar_tier(count: int, max_count: int) -> tuple[str, str]:
    if count <= 10:
        return _BAR_TIERS["gold"]
    if count < max_count * 0.5:
        return _BAR_TIERS["good"]
    if count < max_count * 0.83:
        return _BAR_TIERS["normal"]
    return _BAR_TIERS["bad"]


def _draw_badge(canvas: skia.Canvas, text: str, x: float, y: float, h: float, bg: str, fg: str) -> float:
    f = base.font(11)
    bw = base.measure(text, f) + 14
    base.draw_rounded(canvas, x, y, bw, h, h / 2, base.color(bg))
    base.draw_text_center(canvas, text, x + bw / 2, base.baseline_center(y, h, f), f, base.color(fg))
    return bw


async def render_gacha_detail(
    data: dict,
    uid: str,
    game: str,
    pool_label: str,
    face: dict | None = None,
) -> bytes:
    """渲染抽卡记录详情卡片，返回 PNG bytes

    data: analyse_pool 的输出；face: 可选 {"name","face","elem"}（头像/元素配色）
    """
    stat = data.get("stat") or {}
    five_log = data.get("fiveLog") or []
    items = data.get("items") or {}
    max_four = data.get("maxFour") or {"name": "无", "count": 0}
    no_wai_rate = data.get("noWaiRate", 0)

    face = face or {}
    elem = face.get("elem") if game == "gs" else "sr"
    bg_colors = base.elem_gradient(elem)
    is_weapon = any(k in pool_label for k in ("武器", "光锥"))
    max_count = 80 if is_weapon else 90

    # 预取图片（失败走占位分支）
    face_img = await base.fetch_image(face.get("face", "")) if face.get("face") else None
    icons: dict[Any, skia.Image | None] = {}
    for ds in five_log:
        item = items.get(ds["id"]) or items.get(str(ds["id"])) or {}
        icons[ds["id"]] = await base.fetch_image(item.get("img", ""))

    # 统计六项（对照需求：五星数/平均出金/UP平均/小保底不歪率/已垫/UP花费）
    stat_cells = [
        ("五星数", stat.get("fiveNum", 0)),
        ("平均出金", stat.get("fiveAvg", 0)),
        ("UP平均", stat.get("isvalidNum", 0)),
        ("小保底不歪率", f"{no_wai_rate}%" if no_wai_rate else "0%"),
        ("已垫", stat.get("noFiveNum", 0)),
        ("UP花费", stat.get("upYs", 0)),
    ]

    def builder(canvas: skia.Canvas, w: int, h: int) -> int:
        if h > 0:
            base.draw_gradient(canvas, 0, 0, w, h, bg_colors)
        y = float(M)

        # ---------------- 头部（对照 user-banner） ----------------
        head_h = 100
        avatar = 72
        base.draw_image_or_placeholder(
            canvas, face_img, (M + 10, y + 14, avatar, avatar), face.get("name", "旅行者"), r=avatar / 2
        )
        tx = M + 10 + avatar + 18
        base.draw_text(canvas, "抽卡记录分析", tx, y + 46, base.font(30, "title"), base.color(base.TEXT_MAIN))
        sub = f"{pool_label} · UID {uid}"
        base.draw_text_ellipsis(canvas, sub, tx, y + 76, w - tx - M, base.font(15), base.color(base.GOLD))
        y += head_h

        # ---------------- 统计面板（对照 user-banner .stat） ----------------
        stat_h = 76
        base.draw_shadow(canvas, base.rrect(M, y, w - 2 * M, stat_h, 10))
        base.draw_rounded(canvas, M, y, w - 2 * M, stat_h, 10, base.color("#73000000"))
        cell_w = (w - 2 * M) / len(stat_cells)
        for i, (label, value) in enumerate(stat_cells):
            cx = M + cell_w * i + cell_w / 2
            vf = base.num_font(value, 21)
            base.draw_text_center(canvas, value, cx, y + 32, vf, base.color(base.NUM_GOLD))
            base.draw_text_center(canvas, label, cx, y + 58, base.font(12), base.color(base.TEXT_SUB))
        y += stat_h + 12

        # ---------------- 五星列表（对照 .gacha-list） ----------------
        row_h, gap = 44, 6
        prev_date = None
        for ds in five_log:
            item = items.get(ds["id"]) or items.get(str(ds["id"])) or {}
            is_placeholder = ds["id"] == 888
            is_up = bool(ds.get("isUp"))
            count = int(ds.get("count") or 0)

            base.draw_rounded(canvas, M, y, w - 2 * M, row_h, 6, base.color("#66000000"))
            # 日期（同一天的后缀条目降透明度，对照原版 has-date/no-date）
            date_c = base.GOLD if is_up else base.TEXT_SUB
            if ds.get("date") == prev_date:
                date_c = "#66aaaaaa"
            base.draw_text(canvas, ds.get("date", ""), M + 10, base.baseline_center(y, row_h, base.font(13)),
                           base.font(13), base.color(date_c))
            prev_date = ds.get("date")
            # 图标（星级底色 + 角色/武器图，无图占位首字）
            icon = 36
            ix, iy = M + 68, y + (row_h - icon) / 2
            star = item.get("star", 5)
            base.draw_rounded(canvas, ix, iy, icon, icon, 8, base.color(base.STAR_BG.get(star, base.STAR_BG[5])))
            base.draw_image_or_placeholder(canvas, icons.get(ds["id"]), (ix + 2, iy + 2, icon - 4, icon - 4),
                                           item.get("abbr", ""), r=6)
            # 名字（UP 金色 / 歪 灰白，对照 .up/.wai 的 .name 配色）
            name = item.get("abbr") or item.get("name") or "未知"
            name_c = "#ffd484" if is_up else "#dddddd"
            nf = base.font(15)
            name_w = 92
            base.draw_text_ellipsis(canvas, name, ix + icon + 8, base.baseline_center(y, row_h, nf),
                                    name_w, nf, base.color(name_c))
            # 进度条（对照 .process .bar）
            bx = ix + icon + 8 + name_w + 10
            bw_total = w - M - bx - 8
            bh = 24
            by = y + (row_h - bh) / 2
            base.draw_rounded(canvas, bx, by, bw_total, bh, 6, base.color("#59000000"))
            label = f"已抽 {count} 抽" if is_placeholder else str(count)
            # num_font 按文本内容选字库（tttgbnumber 无中文字形，含中文的占位标签会退回 default）
            cf = base.num_font(label, 15)
            # 填充条至少能完整容纳文字（占位行"已抽 N 抽"较长，避免金色条压住文字）
            fill_w = max(30.0, bw_total * min(count, max_count) / max_count, base.measure(label, cf) + 16)
            bg, fg = _bar_tier(count, max_count)
            base.draw_rounded(canvas, bx, by, fill_w, bh, 6, base.color(bg))
            base.draw_text(canvas, label, bx + 8, base.baseline_center(by, bh, cf), cf, base.color(fg))
            # UP / 歪 角标（条内右端）
            if not is_placeholder:
                btext, bbg, bfg = UP_BADGE if is_up else WAI_BADGE
                _draw_badge(canvas, btext, bx + bw_total - 40, by + 2, bh - 4, bbg, bfg)
            y += row_h + gap

        # ---------------- 底部四星摘要 + 水印 ----------------
        foot_h = 44
        base.draw_rounded(canvas, M, y, w - 2 * M, foot_h, 8, base.color("#73000000"))
        four_txt = (
            f"紫卡 {stat.get('fourNum', 0)} · 平均出紫 {stat.get('fourAvg', 0)}"
            f" · 最多四星 {max_four.get('name', '无')}×{max_four.get('count', 0)}"
        )
        ff = base.font(14)
        base.draw_text_center(canvas, four_txt, w / 2, base.baseline_center(y, foot_h, ff), ff,
                              base.color(base.TEXT_MAIN))
        y += foot_h + 10

        wm = f"生成于 {datetime.now():%Y-%m-%d %H:%M} · nonebot-plugin-miao"
        base.draw_text_center(canvas, wm, w / 2, y + 14, base.font(12), base.color("#99ffffff"))
        y += 34
        return int(y)

    return base.render_card(W, builder)
