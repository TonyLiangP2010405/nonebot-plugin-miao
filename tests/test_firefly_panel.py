"""流萤（加强版 2310 / 基础版 1310）面板回归测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import skia

from nonebot_plugin_miao.core.artis_mark import calc_mark
from nonebot_plugin_miao.core.attr_calc import calc_attr
from nonebot_plugin_miao.core.player import Player
from nonebot_plugin_miao.datasource.mihomo import parse_mihomo
from nonebot_plugin_miao.dmg import calc_dmg
from nonebot_plugin_miao.render.profile_detail import render_profile_detail, stat_rows

FIXTURE = Path(__file__).parent / "fixtures" / "mihomo_800680302.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def player(raw) -> Player:
    return Player("800680302", "sr", parse_mihomo(raw, "800680302"))


def test_enhanced_firefly_lookup_falls_back(player):
    # mihomo enhancedId=1 会把 1310 流萤解析成 2310 流萤Pro
    pro = player.get_avatar("流萤")
    assert pro is not None
    assert pro["id"] == 2310 and pro["name"] == "流萤Pro"
    assert player.get_avatar("流萤Pro")["id"] == 2310
    assert player.get_avatar("流莹")["id"] == 2310


def test_base_firefly_lookup_falls_back(raw):
    base_raw = copy.deepcopy(raw)
    for item in base_raw["detailInfo"].get("assistAvatarList", []):
        if item.get("avatarId") == 1310:
            item.pop("enhancedId", None)
    player = Player("800680302", "sr", parse_mihomo(base_raw, "800680302"))
    assert player.get_avatar("流萤")["id"] == 1310
    assert player.get_avatar("流萤Pro")["id"] == 1310


def test_firefly_attr_and_stat_rows(player):
    avatar = player.get_avatar("流萤")
    attr = calc_attr(avatar, "sr")
    assert attr["recharge"] == pytest.approx(100.0)
    assert attr["stance"] == pytest.approx(246.4200004, rel=1e-6)
    mark = calc_mark(avatar, attr, "sr")
    rows = {row["key"]: row for row in stat_rows(avatar, attr, mark, "sr")}
    assert rows["recharge"]["label"] == "能量恢复效率"
    assert rows["recharge"]["value"] == "100.0%"
    assert rows["stance"]["value"] == "246.4%"
    assert "治疗加成" in next(row["label"] for row in stat_rows(avatar, attr, mark, "sr") if row["key"] == "heal")


def test_firefly_mark_and_damage(player):
    avatar = player.get_avatar("流萤")
    attr = calc_attr(avatar, "sr")
    mark = calc_mark(avatar, attr, "sr")
    assert mark["mark"] > 0
    assert mark["markClass"] in {"SSS", "ACE", "MAX"}
    dmg = calc_dmg(avatar, "sr")
    assert dmg["selected"]["avg"] > 0
    assert dmg["selected"]["title"] == "破韧后战技主目标伤害"


async def test_render_firefly_panel(player):
    avatar = player.get_avatar("流萤")
    attr = calc_attr(avatar, "sr")
    mark = calc_mark(avatar, attr, "sr")
    dmg = calc_dmg(avatar, "sr")
    png = await render_profile_detail(avatar, attr, mark, "sr", dmg, uid="800680302")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = skia.Image.MakeFromEncoded(png)
    assert image is not None
    assert image.height() > 1200
    # 详情卡必须有深色不透明底，否则透明 PNG 在浅色客户端上会看不清白字
    pixels = image.toarray()
    assert pixels[600, 10, 3] == 255
    assert tuple(pixels[600, 10, :3]) != (255, 255, 255)
