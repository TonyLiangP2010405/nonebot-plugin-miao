"""meta 元数据加载器单元测试：基于 resources 下的真实元数据文件"""
import pytest

from nonebot_plugin_miao.core import meta

# ---------------- 角色查找（gs） ----------------


def test_get_character_gs_by_name():
    keqing = meta.get_character("刻晴", "gs")
    assert keqing is not None
    assert keqing.id == 10000042
    assert keqing.name == "刻晴"
    assert keqing.star == 5
    assert keqing.elem == "electro"
    assert keqing.weapon == "sword"
    # data.json 原样透传
    assert "baseAttr" in keqing.data and "talent" in keqing.data and "talentId" in keqing.data


def test_get_character_gs_by_alias():
    # refs/miao-plugin/resources/meta-gs/character/alias.js：迪卢克的别名含「卢姥爷」
    diluc = meta.get_character("卢姥爷", "gs")
    assert diluc is not None
    assert diluc.name == "迪卢克"
    # 英文名也是别名
    assert meta.get_character("Diluc", "gs").name == "迪卢克"


def test_get_character_gs_by_id():
    assert meta.get_character("10000042", "gs").name == "刻晴"
    assert meta.get_character(10000089, "gs").name == "芙宁娜"


def test_get_character_furina():
    furina = meta.get_character("芙宁娜", "gs")
    assert furina is not None
    assert furina.id == 10000089
    assert furina.star == 5


def test_get_character_not_found():
    assert meta.get_character("不存在的角色", "gs") is None
    assert meta.get_character("", "sr") is None


def test_list_characters():
    characters = meta.list_characters("gs")
    names = [item["name"] for item in characters]
    assert "芙宁娜" in names
    assert len(names) == len(set(names))
    assert characters == sorted(characters, key=lambda item: (-int(item.get("star") or 0), item["name"]))


# ---------------- 角色查找（sr） ----------------


def test_get_character_sr():
    kafka = meta.get_character("卡芙卡", "sr")
    assert kafka is not None
    assert kafka.id == 1005
    assert kafka.star == 5
    assert kafka.weapon == "虚无"
    # sr 特有字段
    assert "tree" in kafka.data and "sp" in kafka.data


def test_get_character_sr_alias_and_abbr():
    # meta-sr/character/alias.js：穹·存护 的别名含「火主」
    assert meta.get_character("火主", "sr").name == "穹·存护"
    # meta-sr/character/alias.js 的 abbr 表：丹恒•饮月 -> 饮月君
    assert meta.get_character("饮月君", "sr").name == "丹恒•饮月"


def test_get_character_invalid_game():
    with pytest.raises(ValueError):
        meta.get_character("刻晴", "xx")


# ---------------- 武器 ----------------


def test_get_weapon_gs():
    sword = meta.get_weapon("雾切之回光", "gs")
    assert sword is not None
    assert sword["id"] == 11509
    assert sword["star"] == 5
    assert sword["type"] == "sword"
    assert "attr" in sword


def test_get_weapon_gs_by_abbr():
    # meta-gs/weapon/alias.js：雾切之回光 -> 雾切
    assert meta.get_weapon("雾切", "gs")["name"] == "雾切之回光"


def test_get_weapon_sr():
    weapon = meta.get_weapon("此身为剑", "sr")
    assert weapon is not None
    assert weapon["star"] == 5
    assert weapon["type"] == "毁灭"


def test_get_weapon_not_found():
    assert meta.get_weapon("不存在的武器", "gs") is None


def test_list_weapons():
    weapons = meta.list_weapons("gs")
    names = [item["name"] for item in weapons]
    assert "雾切之回光" in names
    assert len(names) == len(set(names))
    assert all(item.get("type") in {"sword", "claymore", "polearm", "bow", "catalyst"} for item in weapons)


# ---------------- 圣遗物 / 遗器 ----------------


def test_get_artifact_meta():
    gs_arti = meta.get_artifact_meta("gs")
    assert isinstance(gs_arti, dict) and len(gs_arti) > 0
    sr_arti = meta.get_artifact_meta("sr")
    assert isinstance(sr_arti, dict) and len(sr_arti) > 0


def test_artifact_extra_gs():
    extra = meta.artifact_extra("gs")
    for key in ("attrMap", "mainAttr", "subAttr", "attrPct", "basicNum"):
        assert key in extra
    attr_map = extra["attrMap"]
    for key in ("hp", "atk", "cpct", "cdmg", "mastery", "recharge"):
        assert key in attr_map
    # lodash.forEach 的副作用也已求值：value/text 已计算
    assert attr_map["hp"]["value"] == pytest.approx(3.885 * 1.5)
    assert attr_map["hp"]["text"].endswith("%")


def test_artifact_extra_sr():
    extra = meta.artifact_extra("sr")
    attr_map = extra["attrMap"]
    for key in ("hp", "atk", "cpct", "speed", "stance", "effPct"):
        assert key in attr_map
    assert "speed" in extra["subAttr"]


# ---------------- 卡池 ----------------


def test_pool_data_gs():
    pools = meta.pool_data("gs")
    assert isinstance(pools, list) and len(pools) > 0
    first = pools[0]
    for key in ("version", "from", "to", "char5", "char4"):
        assert key in first
    assert first["version"] == "1.0"
    assert first["char5"] == ["温迪"]
    # 原始导出含 poolName 与 mixPoolDetail（集录祈愿）
    info = meta.pool_info("gs")
    assert info["poolName"]["温迪"] == "杯装之诗"
    assert len(info["mixPoolDetail"]) > 0


def test_pool_data_sr():
    pools = meta.pool_data("sr")
    assert isinstance(pools, list) and len(pools) > 0
    first = pools[0]
    for key in ("version", "from", "to", "char5", "char4"):
        assert key in first
    assert first["char5"] == ["希儿"]
    assert meta.pool_info("sr")["poolNameSr"]["希儿"] == "蝶立锋锷"


# ---------------- 模拟抽卡配置 ----------------


def test_gacha_sim_config():
    cfg = meta.gacha_sim_config()
    assert set(cfg.keys()) == {"gacha", "pool", "set"}
    assert cfg["gacha"]["chance5"] == 60
    assert len(cfg["pool"]) > 0
    assert "default" in cfg["set"]


# ---------------- 图片路径助手 ----------------


def test_char_img_gs():
    assert meta.char_img("刻晴", "face", "gs") == "meta-gs/character/刻晴/imgs/face.webp"
    assert meta.char_img("刻晴", "qFace", "gs") == "meta-gs/character/刻晴/imgs/face-q.webp"
    assert meta.char_img("刻晴", "gacha", "gs") == "meta-gs/character/刻晴/imgs/gacha.webp"
    assert meta.char_img("刻晴", "splash", "gs") == "meta-gs/character/刻晴/imgs/splash.webp"
    assert meta.char_img("刻晴", "banner", "gs") == "meta-gs/character/刻晴/imgs/banner.webp"
    assert meta.char_img("刻晴", "cons3", "gs") == "meta-gs/character/刻晴/icons/cons-3.webp"
    assert meta.char_img("刻晴", "passive0", "gs") == "meta-gs/character/刻晴/icons/passive-0.webp"
    assert meta.char_img("刻晴", "talent-e", "gs") == "meta-gs/character/刻晴/icons/talent-e.webp"
    # 支持别名解析为规范名
    assert meta.char_img("卢姥爷", "face", "gs") == "meta-gs/character/迪卢克/imgs/face.webp"


def test_char_img_sr():
    assert meta.char_img("卡芙卡", "face", "sr") == "meta-sr/character/卡芙卡/imgs/face.webp"
    assert meta.char_img("卡芙卡", "preview", "sr") == "meta-sr/character/卡芙卡/imgs/preview.webp"
    assert meta.char_img("卡芙卡", "tree1", "sr") == "meta-sr/character/卡芙卡/imgs/tree-1.webp"
    assert meta.char_img("卡芙卡", "cons2", "sr") == "meta-sr/character/卡芙卡/imgs/cons-2.webp"
    assert meta.char_img("卡芙卡", "talent-a", "sr") == "meta-sr/character/卡芙卡/imgs/talent-a.webp"
    # banner/card 是 sr 公共资源
    assert meta.char_img("卡芙卡", "banner", "sr") == "meta-sr/character/common/imgs/banner.webp"
    assert meta.char_img("卡芙卡", "card", "sr") == "meta-sr/character/common/imgs/card.webp"


def test_char_img_bad_kind():
    with pytest.raises(ValueError):
        meta.char_img("刻晴", "not-a-kind", "gs")


def test_weapon_img():
    assert meta.weapon_img("雾切之回光", "icon", "gs") == "meta-gs/weapon/sword/雾切之回光/icon.webp"
    assert meta.weapon_img("雾切", "awaken", "gs") == "meta-gs/weapon/sword/雾切之回光/awaken.webp"
    assert meta.weapon_img("雾切之回光", "gacha", "gs") == "meta-gs/weapon/sword/雾切之回光/gacha.webp"
    assert meta.weapon_img("此身为剑", "gacha", "sr") == "meta-sr/weapon/毁灭/此身为剑/splash.webp"
    with pytest.raises(ValueError):
        meta.weapon_img("不存在的武器", "icon", "gs")


# ---------------- _eval_esm ----------------


def test_eval_esm_basic(tmp_path):
    js = tmp_path / "sample.js"
    js.write_text(
        "import lodash from 'lodash'\n"
        "export const a = { x: 1, y: '中文' }\n"
        "export const b = 'p,q'.split(',')\n"
        "export default { z: [1, 2] }\n",
        encoding="utf-8",
    )
    result = meta._eval_esm(js, ["a", "b", "__default__", "missing"])
    assert result["a"] == {"x": 1, "y": "中文"}
    assert result["b"] == ["p", "q"]
    assert result["__default__"] == {"z": [1, 2]}
    assert "missing" not in result  # 未定义的导出被 typeof 守卫丢弃


def test_eval_esm_real_alias():
    # 真实文件：meta-gs/character/alias.js
    exports = meta._eval_esm(meta.RES_DIR / "meta-gs" / "character" / "alias.js", ["alias"])
    assert "卢姥爷" in exports["alias"]["迪卢克"].split(",")
