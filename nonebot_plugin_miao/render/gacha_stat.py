"""按版本统计卡片（对照 refs/miao-plugin resources/gacha/gacha-stat.html）

布局对应关系（HTML -> 手绘）：
- user-banner 的 totalStat 六项 -> 头部汇总面板（总抽数/金卡/UP角色/UP武器/紫卡/平均UP抽）
- 每个 versionData 一个 .cont 块 -> 版本块：标题行（版本号+上下半+UP角色名+时间区间 + 右侧 stats 数字）
  + .gacha-stat.card-list 物品图标条（图标 + 数量角标 item-life，UP 卡金色描边 up-card）
- 集录池 isMix 时标题改为"集录祈愿统计"
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import skia

from . import base

W = 720
M = 16

# 头部汇总（对照 gacha-stat.html statMap）
_TOTAL_MAP = [
    ("totalNum", "抽卡总数"),
    ("star5Num", "金卡"),
    ("c5UpNum", "UP角色"),
    ("w5UpNum", "UP武器"),
    ("star4Num", "紫卡"),
    ("avgUpNum", "平均UP抽"),
]
# 版本块内 stats（对照 keyMap）
_KEY_MAP = [
    ("totalNum", "总抽卡"),
    ("star5Num", "金卡"),
    ("upNum", "UP金卡"),
    ("c4Num", "紫角色"),
    ("w4Num", "紫武器"),
]


def _draw_stat_cells(canvas: skia.Canvas, cells: list[tuple[str, Any]], x: float, y: float, w: float) -> None:
    cell_w = w / len(cells)
    for i, (label, value) in enumerate(cells):
        cx = x + cell_w * i + cell_w / 2
        vf = base.num_font(value, 20)
        base.draw_text_center(canvas, value, cx, y + 30, vf, base.color(base.NUM_GOLD))
        base.draw_text_center(canvas, label, cx, y + 54, base.font(12), base.color(base.TEXT_SUB))


async def render_gacha_stat(data: dict, uid: str, game: str) -> bytes:
    """渲染按版本统计卡片，返回 PNG bytes

    data: stat_pool 的输出（{versionData, itemMap, totalStat, isMix}）
    """
    version_data = data.get("versionData") or []
    item_map = data.get("itemMap") or {}
    total_stat = data.get("totalStat") or {}
    is_mix = bool(data.get("isMix"))

    def _item(item_id: Any) -> dict:
        return item_map.get(item_id) or item_map.get(str(item_id)) or {}

    # 预取所有 4/5 星物品图标
    icons: dict[Any, skia.Image | None] = {}
    for v in version_data:
        for ds in v.get("items") or []:
            item = _item(ds["id"])
            if item.get("star", 0) >= 4:
                icons[ds["id"]] = await base.fetch_image(item.get("img", ""))

    total_cells = [(label, total_stat.get(key, 0)) for key, label in _TOTAL_MAP if total_stat.get(key)]
    if not total_cells:
        total_cells = [("totalNum", "抽卡总数"), ("star5Num", "金卡")]
        total_cells = [(label, total_stat.get(key, 0)) for key, label in total_cells]

    title = "集录祈愿统计" if is_mix else "抽卡统计"
    bg_colors = base.elem_gradient("sr" if game == "sr" else "hydro")

    icon_size = 56  # 物品图标边长
    card_w = 66  # 物品卡宽（对照 gs cardWidth=69）
    card_h = icon_size + 26  # 图标 + 名字行
    grid_gap = 8

    def _version_items(v: dict) -> list[dict]:
        return [ds for ds in (v.get("items") or []) if _item(ds["id"]).get("star", 0) >= 4]

    def builder(canvas: skia.Canvas, w: int, h: int) -> int:
        if h > 0:
            base.draw_gradient(canvas, 0, 0, w, h, bg_colors)
        y = float(M)
        inner = w - 2 * M

        # ---------------- 头部汇总（对照 user-banner + totalStat） ----------------
        base.draw_text(canvas, title, M + 4, y + 32, base.font(28, "title"), base.color(base.TEXT_MAIN))
        base.draw_text(canvas, f"UID {uid}", w - M - base.measure(f"UID {uid}", base.font(15)),
                       y + 32, base.font(15), base.color(base.GOLD))
        y += 48
        head_h = 72
        base.draw_shadow(canvas, base.rrect(M, y, inner, head_h, 10))
        base.draw_rounded(canvas, M, y, inner, head_h, 10, base.color("#73000000"))
        _draw_stat_cells(canvas, total_cells, M, y, inner)
        y += head_h + 12

        # ---------------- 版本块 ----------------
        cols = max(1, int((inner + grid_gap) // (card_w + grid_gap)))
        for v in version_data:
            stats = v.get("stats") or {}
            v_items = _version_items(v)
            cells = [(label, stats.get(key, 0)) for key, label in _KEY_MAP if stats.get(key, 0) > 0]

            # 标题行（对照 .cont-title .gacha-pool：version + pool-name + stat-info）
            title_h = 56
            base.draw_shadow(canvas, base.rrect(M, y, inner, title_h, 10))
            base.draw_rounded(canvas, M, y, inner, title_h, 10, base.color("#99000000"))
            vf = base.font(20, "title")
            version_txt = f"{v.get('version', '')} {v.get('half', '')}".strip()
            base.draw_text(canvas, version_txt, M + 14, y + 26, vf, base.color(base.GOLD))
            sub_f = base.font(12)
            if v.get("from"):
                time_txt = f"{v['from']} ~ {v['to']}"
                base.draw_text(canvas, time_txt, M + 14, y + 46, sub_f, base.color(base.TEXT_SUB))
            # UP 角色名（池名）
            name_txt = v.get("name") or ""
            name_x = M + 150
            base.draw_text_ellipsis(canvas, name_txt, name_x, y + 30, 190, base.font(16),
                                    base.color(base.TEXT_MAIN))
            # 右侧 stats 数字（对照 .stat-info .info）
            rx = w - M - 12
            for label, value in reversed(cells):
                nf = base.num_font(value, 20)
                val_w = base.measure(str(value), nf)
                lw = base.measure(label, base.font(11))
                cw = max(val_w, lw) + 8
                rx -= cw
                base.draw_text_center(canvas, value, rx + cw / 2, y + 26, nf, base.color(base.NUM_GOLD))
                base.draw_text_center(canvas, label, rx + cw / 2, y + 46, base.font(11), base.color(base.TEXT_SUB))
            y += title_h

            # 物品图标条（对照 .gacha-stat.card-list）
            if v_items:
                rows = (len(v_items) + cols - 1) // cols
                grid_h = rows * (card_h + grid_gap) + 10
                base.draw_rounded(canvas, M, y, inner, grid_h, 10, base.color("#66000000"))
                gx = M + 10
                gy = y + 10
                for i, ds in enumerate(v_items):
                    item = _item(ds["id"])
                    star = item.get("star", 4)
                    is_up = bool(ds.get("isUp"))
                    cx0 = gx + (i % cols) * (card_w + grid_gap)
                    cy0 = gy + (i // cols) * (card_h + grid_gap)
                    # UP 卡金色描边（对照 .up-card 的 box-shadow）
                    if is_up:
                        base.draw_rounded(canvas, cx0 - 2, cy0 - 2, card_w + 4, card_h + 4, 8, base.color("#fff100"))
                    base.draw_rounded(canvas, cx0, cy0, card_w, card_h, 6, base.color("#e6e6e6"))
                    base.draw_rounded(canvas, cx0 + 3, cy0 + 3, card_w - 6, icon_size - 4, 5,
                                      base.color(base.STAR_BG.get(star, base.STAR_BG[4])))
                    base.draw_image_or_placeholder(
                        canvas, icons.get(ds["id"]), (cx0 + 5, cy0 + 5, card_w - 10, icon_size - 8),
                        item.get("abbr", ""), r=4)
                    # 数量角标（对照 .item-life：UP 金底棕字 / 普通黑底白字）
                    num_txt = str(ds.get("num", 0))
                    nf = base.num_font(num_txt, 14)
                    nw = max(22, base.measure(num_txt, nf) + 10)
                    nbg, nfg = ("#ffeb73", "#6f4b00") if is_up else ("#333333", "#ffffff")
                    base.draw_rounded(canvas, cx0 + card_w - nw - 3, cy0 + icon_size - 18, nw, 18, 6,
                                      base.color(nbg))
                    base.draw_text_center(canvas, num_txt, cx0 + card_w - nw / 2 - 3,
                                          base.baseline_center(cy0 + icon_size - 18, 18, nf), nf,
                                          base.color(nfg))
                    # 名字（对照 .item-name：UP 棕金 / 普通黑）
                    name = item.get("name", "")
                    name = name if len(name) <= 4 else (item.get("abbr") or name)
                    name_c = "#6f4b00" if is_up else "#1a1a1a"
                    base.draw_text_ellipsis(canvas, name, cx0 + 4, cy0 + icon_size + 18, card_w - 8,
                                            base.font(12), base.color(name_c))
                y += grid_h
            y += 12

        wm = f"生成于 {datetime.now():%Y-%m-%d %H:%M} · nonebot-plugin-miao"
        base.draw_text_center(canvas, wm, w / 2, y + 14, base.font(12), base.color("#99ffffff"))
        y += 34
        return int(y)

    return base.render_card(W, builder)
