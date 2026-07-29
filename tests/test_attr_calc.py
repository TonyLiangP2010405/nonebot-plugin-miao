"""面板属性计算与圣遗物评分测试：calc_attr / calc_mark / get_char_weight

基准值来自 tools/verify_attr.mjs 交叉验证（与原版 miao-plugin Attr.js +
ArtisMark.js 链路逐字段一致，见 tools/py_attr_out.json）：
- eula：enka fixture 优菈 Lv85 突破6，试作古华 Lv90 精3
- diluc：enka fixture 迪卢克 Lv80 突破5 C1
- jingliu：mihomo fixture 镜流Pro（加强角色 1212→2212）Lv80 晋阶6，此身为剑 叠1
"""
import json
from pathlib import Path

import pytest

from nonebot_plugin_miao.core import meta
from nonebot_plugin_miao.core.artis_mark import calc_mark, get_char_weight, get_mark_class
from nonebot_plugin_miao.core.attr_calc import calc_attr, calc_promote
from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.datasource.mihomo import parse_mihomo

FIXTURES = Path(__file__).parent / "fixtures"


def _avatars(game: str) -> dict:
    if game == "gs":
        raw = json.loads((FIXTURES / "enka_800055548.json").read_text(encoding="utf-8"))
        return parse_enka(raw, "800055548")["avatars"]
    raw = json.loads((FIXTURES / "mihomo_702762444.json").read_text(encoding="utf-8"))
    return parse_mihomo(raw, "702762444")["avatars"]


@pytest.fixture(scope="module")
def gs_avatars() -> dict:
    return _avatars("gs")


@pytest.fixture(scope="module")
def sr_avatars() -> dict:
    return _avatars("sr")


# ---------------------------------------------------------------------------
# calc_attr：逐字段断言（基准 = 交叉验证的 JS 原版输出）
# ---------------------------------------------------------------------------


def test_calc_attr_eula(gs_avatars):
    attr = calc_attr(gs_avatars["10000051"], "gs")
    assert attr["_calc"] is True
    assert attr["hp"] == pytest.approx(19994.766412626206, rel=1e-9)
    assert attr["hpBase"] == pytest.approx(12761.0, rel=1e-9)
    assert attr["atk"] == pytest.approx(2125.9901249107343, rel=1e-9)
    assert attr["atkBase"] == pytest.approx(894.785, rel=1e-9)
    assert attr["def"] == pytest.approx(976.9374312933348, rel=1e-9)
    assert attr["defBase"] == pytest.approx(724.485, rel=1e-9)
    assert attr["mastery"] == pytest.approx(0.0)
    assert attr["cpct"] == pytest.approx(61.34999972760677, rel=1e-9)
    assert attr["cdmg"] == pytest.approx(135.79999882876874, rel=1e-9)
    assert attr["recharge"] == pytest.approx(116.84000045061111, rel=1e-9)
    assert attr["dmg"] == pytest.approx(0.0)
    assert attr["phy"] == pytest.approx(108.275, rel=1e-9)
    assert attr["heal"] == pytest.approx(0.0)
    # staticAttr 为 {key: {base, plus, pct}} 原始累加值，面板图展示 base+plus 用
    assert attr["staticAttr"]["hp"]["base"] == pytest.approx(12761.0, rel=1e-9)
    assert attr["staticAttr"]["atk"]["pct"] > 0  # 试作古华副词条 atkPct
    assert attr["_base"]["atk"] == pytest.approx(894.785, rel=1e-9)


def test_calc_attr_diluc(gs_avatars):
    attr = calc_attr(gs_avatars["10000016"], "gs")
    assert attr["hp"] == pytest.approx(25239.15429688198, rel=1e-9)
    assert attr["atk"] == pytest.approx(1590.8715376418072, rel=1e-9)
    assert attr["def"] == pytest.approx(772.2852235630154, rel=1e-9)
    assert attr["mastery"] == pytest.approx(33.56999969482422, rel=1e-9)
    assert attr["cpct"] == pytest.approx(32.22999999821186, rel=1e-9)
    assert attr["cdmg"] == pytest.approx(68.64999979734421, rel=1e-9)
    assert attr["recharge"] == pytest.approx(166.66999987840651, rel=1e-9)
    assert attr["dmg"] == pytest.approx(34.825140000000005, rel=1e-9)
    assert attr["hpBase"] == pytest.approx(11453.0, rel=1e-9)
    assert attr["atkBase"] == pytest.approx(642.0799999999999, rel=1e-9)


def test_calc_attr_jingliu(sr_avatars):
    attr = calc_attr(sr_avatars["2212"], "sr")
    assert attr["hp"] == pytest.approx(3970.122774898837, rel=1e-9)
    assert attr["hpBase"] == pytest.approx(2600.1360000122895, rel=1e-9)
    assert attr["atk"] == pytest.approx(3732.2245641855507, rel=1e-9)
    assert attr["atkBase"] == pytest.approx(1261.260000045765, rel=1e-9)
    assert attr["def"] == pytest.approx(1175.1781150289899, rel=1e-9)
    assert attr["defBase"] == pytest.approx(882.0000000224449, rel=1e-9)
    assert attr["speed"] == pytest.approx(140.632, rel=1e-9)
    assert attr["speedBase"] == pytest.approx(96.0, rel=1e-9)
    assert attr["cpct"] == pytest.approx(25.412000144656613, rel=1e-9)
    assert attr["cdmg"] == pytest.approx(157.19600019050048, rel=1e-9)
    assert attr["dmg"] == pytest.approx(10.0, rel=1e-9)  # 密林卧雪2件套 ice → 同元素转 dmg
    assert attr["stance"] == pytest.approx(27.864000119999996, rel=1e-9)


def test_calc_promote():
    # 移植 Attr.calcPromote
    assert calc_promote(1, "gs") == 0
    assert calc_promote(20, "gs") == 0
    assert calc_promote(21, "gs") == 1
    assert calc_promote(85, "gs") == 6
    assert calc_promote(90, "gs") == 6
    assert calc_promote(80, "sr") == 6
    assert calc_promote(20, "sr") == 0


# ---------------------------------------------------------------------------
# calc_mark：总分/档位/单件断言（基准同上，评分与原版完全一致）
# ---------------------------------------------------------------------------


def test_calc_mark_eula(gs_avatars):
    avatar = gs_avatars["10000051"]
    mark = calc_mark(avatar, calc_attr(avatar, "gs"), "gs")
    assert mark["classTitle"] == "优菈-通用"
    assert mark["mark"] == pytest.approx(166.1622990081038, rel=1e-9)
    assert mark["markClass"] == "S"
    assert mark["charWeight"] == {"atk": 75, "atkPlus": 75, "cpct": 100, "cdmg": 100, "recharge": 55, "phy": 100}
    expected = {"1": ("A", 26.824321467747744), "2": ("S", 32.42126752699498), "3": ("S", 30.57334267199604),
                "4": ("S", 32.94499382591724), "5": ("SSS", 43.39837351544777)}
    for idx, (cls, val) in expected.items():
        piece = mark["artis"][int(idx)]
        assert piece["markClass"] == cls, idx
        assert piece["mark"] == pytest.approx(val, rel=1e-9), idx
    assert mark["artis"][1]["name"] == "无垢之花"
    assert mark["artis"][1]["set"] == "苍白之火"
    assert mark["artis"][1]["main"]["key"] == "hpPlus"
    assert mark["sets"] == {"苍白之火": 2, "染血的骑士道": 2}


def test_calc_mark_diluc(gs_avatars):
    avatar = gs_avatars["10000016"]
    mark = calc_mark(avatar, calc_attr(avatar, "gs"), "gs")
    assert mark["classTitle"] == "迪卢克-通用"
    assert mark["mark"] == pytest.approx(84.60638473716963, rel=1e-9)
    assert mark["markClass"] == "B"


def test_calc_mark_jingliu(sr_avatars):
    avatar = sr_avatars["2212"]
    mark = calc_mark(avatar, calc_attr(avatar, "sr"), "sr")
    assert mark["classTitle"] == "镜流Pro-通用"
    assert mark["mark"] == pytest.approx(128.72758169205554, rel=1e-9)
    assert mark["markClass"] == "A"
    assert mark["charWeight"] == {"hp": 100, "hpPlus": 100, "speed": 100, "cpct": 100, "cdmg": 100,
                                  "recharge": 50, "dmg": 100}
    assert mark["artis"][4]["markClass"] == "SS"
    assert mark["artis"][5]["mark"] == pytest.approx(4.8, rel=1e-9)
    assert mark["sets"] == {"密林卧雪的猎人": 4, "太空封印站": 2}


def test_get_mark_class():
    assert get_mark_class(0) == "D"
    assert get_mark_class(6.99) == "D"
    assert get_mark_class(7) == "C"
    assert get_mark_class(35) == "SS"
    assert get_mark_class(69.99) == "MAX"
    # JS 在 mark >= 70 时返回 undefined
    assert get_mark_class(70) is None


# ---------------------------------------------------------------------------
# get_char_weight：artis.js 自定义规则 / usefulAttr / 默认权重 / 武器·套装修正
# ---------------------------------------------------------------------------


def test_char_weight_custom_rule_keqing():
    # 刻晴 artis.js：attr.mastery >= 80 走精通规则
    ret = get_char_weight("刻晴", {"mastery": 100}, "gs")
    assert ret["title"] == "刻晴-精通"
    assert ret["attrWeight"] == {"atk": 75, "cpct": 100, "cdmg": 100, "mastery": 75, "dmg": 100}
    # 精通不足走 def 默认分支
    ret = get_char_weight("刻晴", {"mastery": 0}, "gs")
    assert ret["title"] == "刻晴-通用"
    assert ret["attrWeight"]["phy"] == 100


def test_char_weight_useful_attr():
    # 迪卢克无 artis.js，走 usefulAttr 表
    ret = get_char_weight("迪卢克", {}, "gs")
    assert ret["title"] == "迪卢克-通用"
    assert ret["attrWeight"]["mastery"] == 75
    assert ret["attrWeight"]["dmg"] == 100


def test_char_weight_default():
    # usefulAttr 与 artis.js 都没有的角色走默认权重
    from nonebot_plugin_miao.core.artis_mark import _useful_attr

    useful_map = _useful_attr("gs")
    target = None
    for sub in sorted((meta.RES_DIR / "meta-gs" / "character").iterdir()):
        if not sub.is_dir() or not (sub / "data.json").is_file():
            continue
        data = json.loads((sub / "data.json").read_text(encoding="utf-8"))
        if data.get("name") not in useful_map and not (sub / "artis.js").exists():
            target = data.get("name")
            break
    assert target, "未找到符合条件的角色"
    ret = get_char_weight(target, {}, "gs")
    assert ret["attrWeight"] == {"atk": 75, "cpct": 100, "cdmg": 100, "dmg": 100, "phy": 100}
    assert ret["title"].endswith("-通用")


def test_char_weight_weapon_cfg():
    # 携带薙草之稻光时充能权重按精炼提升（weaponCheck）
    ret = get_char_weight("优菈", {"cpct": 50, "cdmg": 100}, "gs", weapon={"name": "薙草之稻光", "affix": 1})
    assert ret["title"] == "优菈-薙刀"
    assert ret["attrWeight"]["recharge"] == 65  # 55 + min(10 + (20-10)*0/4) → 65


def test_char_weight_favonius():
    # 西风系列武器：暴击权重强制提高至 100
    ret = get_char_weight("云堇", {}, "gs", weapon={"name": "西风长枪", "affix": 1})
    assert ret["attrWeight"]["cpct"] == 100
    assert "西风" in ret["title"]


def test_char_weight_emblem4():
    # 绝缘4：充能权重拉高至沙漏最高权重齐平（优菈 atk 75 → recharge 75）
    ret = get_char_weight("优菈", {"cpct": 50, "cdmg": 100}, "gs", abbrs=["绝缘4", "绝缘之旗印4"])
    assert ret["attrWeight"]["recharge"] == 75
    assert "绝缘4" in ret["title"]
