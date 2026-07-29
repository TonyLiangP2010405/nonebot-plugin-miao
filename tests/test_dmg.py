"""QuickJS 伤害规则执行测试。

期望数值是硬编码基准，来自 `node tools/verify_dmg.mjs`（用 node 真跑原版
miao-plugin 伤害链路：models/dmg/*.js + models/ProfileDmg.js + 角色/武器/
圣遗物 calc.js，产物 tools/js_dmg_out.json）。断言容差 0.5%，与交叉验证
误差线一致；当前 Python 移植与原版的实测相对误差为 0。
"""
import json
from pathlib import Path

import pytest

from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.datasource.mihomo import parse_mihomo
from nonebot_plugin_miao.dmg import DamageError, calc_dmg

TOL = 0.005


@pytest.fixture(scope="module")
def gs_avatars() -> dict:
    raw = json.loads((Path(__file__).parent / "fixtures" / "enka_800055548.json").read_text(encoding="utf-8"))
    return parse_enka(raw, "800055548")["avatars"]


@pytest.fixture(scope="module")
def sr_avatars() -> dict:
    raw = json.loads((Path(__file__).parent / "fixtures" / "mihomo_702762444.json").read_text(encoding="utf-8"))
    return parse_mihomo(raw, "702762444")["avatars"]


def _assert_rows(rows: list[dict], expected: list[tuple[str, float, float]]) -> None:
    assert [r["title"] for r in rows] == [e[0] for e in expected]
    for row, (_, dmg, avg) in zip(rows, expected):
        assert row["dmg"] == pytest.approx(dmg, rel=TOL)
        assert row["avg"] == pytest.approx(avg, rel=TOL)


# ---------------------------------------------------------------------------
# 优菈：物理 + 大招光剑（试作古华、苍白之火套装；defDmgIdx=3）
# ---------------------------------------------------------------------------


def test_eula_rows_match_baseline(gs_avatars):
    result = calc_dmg(gs_avatars["10000051"], "gs")
    assert result["character"] == "优菈"
    _assert_rows(
        result["dmgData"],
        [
            ("普攻尾段2次伤害", 12837.02779574173, 9979.634966687898),
            ("E0层长按伤害", 7000.544079393053, 5442.293620624988),
            ("E2层长按伤害", 27329.037202111344, 21245.869340453988),
            ("光降之剑12层伤害", 96259.41316824821, 74833.0392994371),
        ],
    )
    # 未输入序号时按角色规则的 defDmgIdx=3 选中大招
    assert result["selectedIdx"] == 3
    assert result["selected"]["title"] == "光降之剑12层伤害"
    assert result["selected"]["dmg"] > result["selected"]["avg"] > 0
    assert result["dmgMsg"] == ["优菈天赋：E消耗冰涡之剑后降低抗性20%"]


def test_eula_artis_set_buff_loaded(gs_avatars):
    """圣遗物套装 buff（苍白之火）必须进入伤害：缺失时普攻 avg 只有 ~5227（旧实现实测）。"""
    result = calc_dmg(gs_avatars["10000051"], "gs")
    basic = result["dmgData"][0]
    assert basic["avg"] == pytest.approx(9979.634966687898, rel=TOL)
    assert basic["avg"] > 9000


def test_eula_dmg_ret_matrix(gs_avatars):
    """dmgRet：属性增减矩阵（atk/cpct/cdmg 3x3，对角线 na），对齐原版 mode 'dmg'。"""
    result = calc_dmg(gs_avatars["10000051"], "gs")
    dmg_ret = result["dmgRet"]
    assert len(dmg_ret) == 3
    for row in dmg_ret:
        assert len(row) == 3
    for i in range(3):
        assert dmg_ret[i][i]["type"] == "na"
        for j in range(3):
            if i != j:
                assert dmg_ret[i][j]["type"] in ("gt", "lt", "avg")
                assert dmg_ret[i][j]["avg"] > 0


def test_eula_explicit_index(gs_avatars):
    assert calc_dmg(gs_avatars["10000051"], "gs", 1)["selected"]["title"] == "普攻尾段2次伤害"
    assert calc_dmg(gs_avatars["10000051"], "gs", 4)["selected"]["title"] == "光降之剑12层伤害"


# ---------------------------------------------------------------------------
# 迪卢克：蒸发（倍率 1.5 + 精通加成必须生效；defDmgIdx=1）
# ---------------------------------------------------------------------------


def test_diluc_rows_match_baseline(gs_avatars):
    result = calc_dmg(gs_avatars["10000016"], "gs")
    assert result["character"] == "迪卢克"
    _assert_rows(
        result["dmgData"],
        [
            ("E三段伤害", 3284.9556591861906, 2378.7616375743014),
            ("E三段蒸发", 5247.950218865326, 3800.2408408852516),
            ("Q爆发伤害", 5209.376235224037, 3772.30794862909),
            ("开Q后单次重击", 1803.912747380576, 1306.2819977494735),
        ],
    )
    assert result["selectedIdx"] == 1
    assert result["selected"]["title"] == "E三段蒸发"


def test_diluc_vaporize_multiplier_applied(gs_avatars):
    """蒸发行必须真的吃到 1.5 倍率 + 精通加成（≈1.597 倍），而非与基础行相等。"""
    result = calc_dmg(gs_avatars["10000016"], "gs")
    basic, vaporize = result["dmgData"][0], result["dmgData"][1]
    ratio = vaporize["avg"] / basic["avg"]
    assert 1.55 < ratio < 1.65
    assert vaporize["avg"] == pytest.approx(3800.2408408852516, rel=TOL)
    # 圣遗物套装 buff（角斗士4）与精通提示都进了 dmgMsg
    assert "角斗士的终幕礼4：角色普通攻击造成的伤害提高35%" in result["dmgMsg"]
    assert any("蒸发" in m for m in result["dmgMsg"])


# ---------------------------------------------------------------------------
# 刻晴：超激化 + 黑剑武器特效（defDmgIdx=3）
# ---------------------------------------------------------------------------


def test_keqing_rows_match_baseline(gs_avatars):
    result = calc_dmg(gs_avatars["10000042"], "gs")
    assert result["character"] == "刻晴"
    _assert_rows(
        result["dmgData"],
        [
            ("E后重击伤害", 808.6256565915281, 553.5455534597908),
            ("Q单段伤害", 98.24823085898124, 73.2832160385214),
            ("Q总伤害", 1919.115442778767, 1431.4654866191179),
            ("Q总伤害·超激化", 4363.476769706381, 3254.711133195238),
        ],
    )
    assert result["selectedIdx"] == 3
    assert result["selected"]["title"] == "Q总伤害·超激化"


def test_keqing_aggravate_and_weapon_buff(gs_avatars):
    """超激化加成显著大于基础行；黑剑（武器 calc.js）buff 进入 dmgMsg。"""
    result = calc_dmg(gs_avatars["10000042"], "gs")
    rows = {r["title"]: r for r in result["dmgData"]}
    assert rows["Q总伤害·超激化"]["avg"] > rows["Q总伤害"]["avg"] * 2
    assert "黑剑：普攻与重击的造成的伤害提升20%" in result["dmgMsg"]
    assert any("超激化" in m for m in result["dmgMsg"])


# ---------------------------------------------------------------------------
# 镜流Pro：星铁链路（sr 武器/套装 buff、月色层数、selectedIdx=-1 的原版行为）
# ---------------------------------------------------------------------------


def test_jingliu_rows_match_baseline(sr_avatars):
    result = calc_dmg(sr_avatars["2212"], "sr")
    assert result["character"] == "镜流Pro"
    _assert_rows(
        result["dmgData"],
        [
            ("普攻伤害", 7253.964723555716, 5825.520987283927),
            ("战技伤害", 21761.894170667147, 17476.56296185178),
            ("终结技伤害(扩散)", 59100.723116127614, 47462.66572797641),
            ("转魄·战技(5层月色, 扩散)", 43523.78834133429, 34953.12592370356),
            ("转魄·终结技(5层月色, 扩散)", 59100.723116127614, 47462.66572797641),
            ("霜魄·转魄战技(5层月色, 扩散)", 50199.216001048146, 40314.034807707176),
        ],
    )
    # 原版 dmgCfg.userIdx = detail.userIdx || defDmgIdx（0 是 falsy → defDmgIdx=-1）
    assert result["selectedIdx"] == -1
    assert result["selected"]["title"] == "普攻伤害"


def test_jingliu_sr_buffs_loaded(sr_avatars):
    """星铁武器（此身为剑）与遗器套装（密林卧雪的猎人4/太空封印站2）buff 进入 dmgMsg。"""
    result = calc_dmg(sr_avatars["2212"], "sr")
    assert any("此身为剑" in m for m in result["dmgMsg"])
    assert "密林卧雪的猎人4：释放终结技后2回合，爆伤提高25%" in result["dmgMsg"]
    assert "太空封印站2：速度大于等于120提高攻击力12%" in result["dmgMsg"]


def test_jingliu_explicit_index(sr_avatars):
    assert calc_dmg(sr_avatars["2212"], "sr", 4)["selected"]["title"] == "转魄·战技(5层月色, 扩散)"


# ---------------------------------------------------------------------------
# 瓦尔特Pro：星铁加强角色（data.json 的 attr 为数组形态，覆盖该兼容路径）
# ---------------------------------------------------------------------------


def test_welt_rows_match_baseline(sr_avatars):
    result = calc_dmg(sr_avatars["2004"], "sr")
    assert result["character"] == "瓦尔特Pro"
    _assert_rows(
        result["dmgData"],
        [
            ("普攻伤害", 11761.516504572468, 8247.843353229364),
            ("战技伤害", 27868.71418751887, 19543.125156115377),
            ("终结技伤害", 9364.48729419317, 6566.91033471619),
            ("天赋附加伤害", 6242.991529462114, 4377.940223144126),
        ],
    )
    assert result["selectedIdx"] == 1
    assert result["selected"]["title"] == "战技伤害"
    # sr 武器（决心如汗珠般闪耀）与遗器套装（快枪手4/太空封印站2）buff 进入 dmgMsg
    assert any("决心如汗珠般闪耀" in m for m in result["dmgMsg"])
    assert "野穗伴行的快枪手4：普攻伤害提高10%" in result["dmgMsg"]


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_calc_dmg_rejects_out_of_range_index(gs_avatars):
    with pytest.raises(DamageError, match="序号输入错误：优菈最多只支持4种伤害计算哦"):
        calc_dmg(gs_avatars["10000051"], "gs", 99)


def test_calc_dmg_unknown_character():
    with pytest.raises(DamageError, match="未找到角色元数据"):
        calc_dmg({"name": "不存在的角色"}, "gs")


def test_calc_dmg_character_without_rule():
    with pytest.raises(DamageError, match="乱破 暂无伤害计算规则"):
        calc_dmg({"name": "乱破"}, "sr")
