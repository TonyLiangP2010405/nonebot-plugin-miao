"""角色详情卡的数据组装测试：天赋/命座图标、武器属性与被动、圣遗物词条与汇总。"""
import json
from pathlib import Path

import pytest

from nonebot_plugin_miao.core import meta
from nonebot_plugin_miao.core.artis_mark import calc_mark
from nonebot_plugin_miao.core.attr_calc import calc_attr
from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.datasource.mihomo import parse_mihomo
from nonebot_plugin_miao.dmg import calc_dmg
from nonebot_plugin_miao.render import profile_detail as pd

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gs_avatars() -> dict:
    raw = json.loads((FIXTURES / "enka_800055548.json").read_text(encoding="utf-8"))
    return parse_enka(raw, "800055548")["avatars"]


@pytest.fixture(scope="module")
def sr_avatars() -> dict:
    raw = json.loads((FIXTURES / "mihomo_702762444.json").read_text(encoding="utf-8"))
    return parse_mihomo(raw, "702762444")["avatars"]


def test_panel_image_paths():
    assert meta.artis_img("苍白之火", 1, "gs") == "meta-gs/artifact/imgs/苍白之火/1.webp"
    assert meta.artis_img("密林卧雪的猎人", 3, "sr") == "meta-sr/artifact/密林卧雪的猎人/arti-3.webp"
    # 原神普攻用武器类型图标，战技/爆发优先用提升该天赋的命座图标
    assert meta.talent_img("钟离", "a", "gs") == "common/item/atk-polearm.webp"
    assert meta.talent_img("钟离", "e", "gs") == "meta-gs/character/钟离/icons/cons-3.webp"
    assert meta.talent_img("钟离", "q", "gs") == "meta-gs/character/钟离/icons/cons-5.webp"
    assert meta.talent_img("镜流Pro", "a", "sr") == "meta-sr/character/镜流Pro/imgs/talent-a.webp"
    # 星铁 3/5 命与天赋共用图标
    assert meta.cons_img("镜流Pro", 3, "sr") == "meta-sr/character/镜流Pro/imgs/talent-q.webp"
    assert meta.cons_img("镜流Pro", 4, "sr") == "meta-sr/character/镜流Pro/imgs/cons-4.webp"
    assert meta.cons_img("钟离", 6, "gs") == "meta-gs/character/钟离/icons/cons-6.webp"


def test_talent_items(gs_avatars, sr_avatars):
    eula = pd.talent_items(gs_avatars["10000051"], "gs")
    assert [item["label"] for item in eula] == ["普攻", "战技", "爆发"]
    assert [item["level"] for item in eula] == [8, 5, 6]
    jingliu = pd.talent_items(sr_avatars["2212"], "sr")
    assert [item["label"] for item in jingliu] == ["普攻", "战技", "终结技", "天赋"]
    assert [item["level"] for item in jingliu] == [6, 10, 10, 10]


def test_cons_items(gs_avatars):
    cons = pd.cons_items(gs_avatars["10000051"], "gs")
    assert len(cons) == 6
    assert all(not item["on"] for item in cons)  # 优菈 C0
    assert cons[0]["img"] == "meta-gs/character/优菈/icons/cons-1.webp"


def test_stat_rows(gs_avatars):
    avatar = gs_avatars["10000051"]
    attr = calc_attr(avatar, "gs")
    mark = calc_mark(avatar, attr, "gs")
    rows = {row["key"]: row for row in pd.stat_rows(avatar, attr, mark, "gs")}
    assert rows["hp"]["value"] == "19,995"
    assert rows["hp"]["sub"] == "12,761 +7,234"
    assert rows["cpct"]["value"] == "61.3%"
    assert rows["cpct"]["weight"] == 100
    assert rows["recharge"]["weight"] == 55


def test_weapon_display_gs(gs_avatars):
    weapon = pd.weapon_display(gs_avatars["10000051"], "gs")
    assert weapon["name"] == "试作古华"
    assert weapon["star"] == 4
    assert weapon["affix_label"] == "精3"
    assert weapon["attrs"] == [("攻击力", "565"), ("攻击力", "27.6%")]
    assert "360%" in weapon["desc"]


def test_weapon_display_sr(sr_avatars):
    weapon = pd.weapon_display(sr_avatars["2212"], "sr")
    assert weapon["name"] == "此身为剑"
    assert weapon["star"] == 5
    assert weapon["affix_label"] == "叠1"
    assert weapon["attrs"] == [("生命值", "1164"), ("攻击力", "582"), ("防御力", "397")]
    # 精炼占位符已按技能表替换
    assert "20.0%" in weapon["desc"]
    assert "12.0%" in weapon["desc"]


def test_artis_rows(gs_avatars):
    avatar = gs_avatars["10000051"]
    mark = calc_mark(avatar, calc_attr(avatar, "gs"), "gs")
    rows = pd.artis_rows(mark, "gs")
    assert len(rows) == 5
    first = rows[0]
    assert first["name"] == "无垢之花"
    assert first["set"] == "苍白之火"
    assert first["img"] == "meta-gs/artifact/imgs/苍白之火/1.webp"
    assert first["main"] == {"title": "小生命", "value": "4779"}
    assert [sub["title"] for sub in first["subs"]] == ["大攻击", "小防御", "暴击伤害", "小攻击"]
    assert first["subs"][2]["upNum"] == 3
    assert first["subs"][2]["weight"] == 100


def test_aggregate_attrs(gs_avatars):
    avatar = gs_avatars["10000051"]
    mark = calc_mark(avatar, calc_attr(avatar, "gs"), "gs")
    rows = pd.aggregate_attrs(mark, "gs")
    assert 0 < len(rows) <= 9
    assert rows[0]["title"] == "小防御"
    assert rows[0]["value"] == pytest.approx(157.4, rel=1e-6)
    assert rows[0]["weight"] == 0  # 优菈不堆小防御
    # 按 eff 降序
    assert all(rows[i]["eff"] >= rows[i + 1]["eff"] for i in range(len(rows) - 1))


def test_trace_columns(sr_avatars):
    cols = pd.trace_columns(sr_avatars["2212"], "sr")
    assert len(cols) == 9
    assert sum(len(col) for col in cols) == 13
    # 第 2/4/6 列是三大行迹图标
    assert cols[2][0]["img"] == "meta-sr/character/镜流Pro/imgs/tree-1.webp"
    assert cols[4][0]["img"] == "meta-sr/character/镜流Pro/imgs/tree-2.webp"
    assert cols[6][0]["img"] == "meta-sr/character/镜流Pro/imgs/tree-3.webp"
    # 属性节点用 meta-sr/public 图标，且镜流全部点亮
    stat_icons = [item["img"] for col in cols for item in col if "public/icons" in item["img"]]
    assert stat_icons
    assert all(item["on"] for col in cols for item in col)


def test_trace_columns_gs_empty(gs_avatars):
    assert pd.trace_columns(gs_avatars["10000051"], "gs") == []


def test_damage_matrix(gs_avatars):
    avatar = gs_avatars["10000051"]
    dmg = calc_dmg(avatar, "gs")
    matrix = pd.damage_matrix(dmg, "gs")
    assert matrix is not None
    assert [h["title"] for h in matrix["headers"]] == ["大攻击", "暴击率", "暴击伤害"]
    assert len(matrix["rows"]) == 3
    for i, row in enumerate(matrix["rows"]):
        assert row[i]["type"] == "na"
        for j, cell in enumerate(row):
            if i != j:
                assert cell["type"] in ("gt", "lt", "avg")
                assert isinstance(cell["val"], float)
    assert matrix["base"]["avg"] == pytest.approx(74833.0392994371)
