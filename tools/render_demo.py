"""渲染 demo：用 tools/fixtures 的抽卡记录 + 固定 rng 的模拟抽卡生成 4 张 PNG 到 outputs/demo/

用法：poetry run python tools/render_demo.py
机器访问不了 jsdelivr 时 fetch_image 会走占位分支（预期行为），不影响出图。
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import tempfile
from pathlib import Path

import nonebot

nonebot.init(driver="~none")

from nonebot_plugin_miao.core import store  # noqa: E402
from nonebot_plugin_miao.core.artis_mark import calc_mark  # noqa: E402
from nonebot_plugin_miao.core.attr_calc import calc_attr  # noqa: E402
from nonebot_plugin_miao.core.player import Player  # noqa: E402
from nonebot_plugin_miao.datasource.enka import parse_enka  # noqa: E402
from nonebot_plugin_miao.gacha import analyse, simulate  # noqa: E402
from nonebot_plugin_miao.render import (  # noqa: E402
    render_artis_list,
    render_gacha_detail,
    render_gacha_stat,
    render_gacha_trial,
    render_profile_detail,
    render_profile_list,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
PROFILE_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "demo"
GS_UID, SR_UID = "100000001", "800000001"


def _check_png(path: Path, data: bytes) -> None:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是合法 PNG"
    assert len(data) > 1000, f"{path.name} 文件过小（{len(data)}B），疑似空白图"
    print(f"  {path}  {len(data) / 1024:.1f} KB")


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 分析/统计数据走 fixture 目录（只读）
    store._data_dir = lambda: FIXTURE_DIR  # noqa: SLF001

    print("[1/7] 原神角色池详情")
    gs_data = analyse.analyse_pool(1, GS_UID, "角色", "gs")
    png = await render_gacha_detail(gs_data, GS_UID, "gs", "角色活动祈愿", face={"name": "演示玩家", "elem": "pyro"})
    _check_png(OUT_DIR / "gacha_detail_gs.png", png)
    (OUT_DIR / "gacha_detail_gs.png").write_bytes(png)

    print("[2/7] 星铁光锥池详情")
    sr_data = analyse.analyse_pool(2, SR_UID, "光锥", "sr")
    png = await render_gacha_detail(sr_data, SR_UID, "sr", "光锥活动跃迁", face={"name": "开拓者"})
    _check_png(OUT_DIR / "gacha_detail_sr.png", png)
    (OUT_DIR / "gacha_detail_sr.png").write_bytes(png)

    print("[3/7] 原神角色统计（按版本，含 UP 金框）")
    stat_data = analyse.stat_pool(1, GS_UID, "角色", "gs")
    png = await render_gacha_stat(stat_data, GS_UID, "gs")
    _check_png(OUT_DIR / "gacha_stat_gs.png", png)
    (OUT_DIR / "gacha_stat_gs.png").write_bytes(png)

    print("[4/7] 十连模拟抽卡（固定 rng）")
    # 模拟抽卡会落盘 sim 状态，数据目录切到临时目录，避免污染 fixtures
    with tempfile.TemporaryDirectory() as tmp:
        store._data_dir = lambda: Path(tmp)  # noqa: SLF001
        rng = random.Random(42).randint
        result = simulate.do_gacha("demo:1", "role", is_master=True, rng=rng)
        assert result["code"] == "ok", result
        png = await render_gacha_trial(result, "演示玩家")
    _check_png(OUT_DIR / "gacha_trial.png", png)
    (OUT_DIR / "gacha_trial.png").write_bytes(png)

    # 面板卡片：真实 Enka fixture，无图片时同样验证占位渲染路径
    profile_raw = json.loads((PROFILE_FIXTURE_DIR / "enka_800055548.json").read_text(encoding="utf-8"))
    parsed = parse_enka(profile_raw, "800055548")
    player = Player("800055548", "gs", parsed)
    avatar = next(iter(player.avatars.values()))
    attr = calc_attr(avatar, "gs")
    mark = calc_mark(avatar, attr, "gs")
    print("[5/7] 原神面板列表")
    png = await render_profile_list(player, "gs")
    _check_png(OUT_DIR / "profile_list.png", png)
    (OUT_DIR / "profile_list.png").write_bytes(png)
    print("[6/7] 原神角色详情")
    png = await render_profile_detail(avatar, attr, mark, "gs")
    _check_png(OUT_DIR / "profile_detail.png", png)
    (OUT_DIR / "profile_detail.png").write_bytes(png)
    print("[7/7] 原神圣遗物列表")
    png = await render_artis_list(player, "gs")
    _check_png(OUT_DIR / "artis_list.png", png)
    (OUT_DIR / "artis_list.png").write_bytes(png)

    print(f"完成，输出目录: {OUT_DIR}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
