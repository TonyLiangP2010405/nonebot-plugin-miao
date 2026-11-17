"""渲染层测试：纯占位分支（fetch_image 打桩为 None）下 3 个 render 函数输出合法 PNG"""
import json
from pathlib import Path

import pytest
import skia

from nonebot_plugin_miao.core import meta, store
from nonebot_plugin_miao.core.artis_mark import calc_mark
from nonebot_plugin_miao.core.attr_calc import calc_attr
from nonebot_plugin_miao.core.player import Player
from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.datasource.mihomo import parse_mihomo
from nonebot_plugin_miao.dmg import calc_dmg
from nonebot_plugin_miao.gacha import analyse, simulate
from nonebot_plugin_miao.render import (
    artis_list,
    base,
    encyclopedia,
    gacha_detail,
    gacha_stat,
    gacha_trial,
    profile_detail,
    profile_list,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tools" / "fixtures"
AVATAR_FIXTURES = Path(__file__).resolve().parent / "fixtures"
GS_UID, SR_UID = "100000001", "800000001"


@pytest.fixture
def data_dir(monkeypatch):
    monkeypatch.setattr(store, "_data_dir", lambda: FIXTURE_DIR)
    return FIXTURE_DIR


@pytest.fixture
def no_images(monkeypatch):
    """fetch_image 打桩为 None：强制走占位分支，不依赖网络/缓存"""

    async def _none(rel):
        return None

    monkeypatch.setattr(base, "fetch_image", _none)
    return _none


def _check_png(data: bytes) -> None:
    assert isinstance(data, bytes) and len(data) > 1000
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = skia.Image.MakeFromEncoded(data)
    assert img is not None
    assert img.width() > 0 and img.height() > 0


# ---------------- base：字体 / 元素配色 ----------------


def test_fonts_load():
    for kind in ("default", "title", "number"):
        f = base.font(20, kind)
        assert f.getTypeface() is not None
        assert f.getTypeface().countGlyphs() > 0
    # tttgbnumber 是数字字库
    assert base.measure("123456", base.font(20, "number")) > 0


def test_elem_gradient():
    for elem in ("pyro", "火", "anemo", "量子", "物理", "sr", None, "不存在的元素", ""):
        colors = base.elem_gradient(elem)
        assert len(colors) == 2
        assert all(isinstance(c, int) for c in colors)
    # 未知元素给默认（hydro）
    assert base.elem_gradient("不存在的元素") == base.elem_gradient("hydro")
    assert base.normalize_elem("火") == "pyro"
    assert base.normalize_elem("物理") == "sr"


def test_render_card_two_pass():
    def builder(canvas, w, h):
        base.draw_text(canvas, "测试", 10, 30, base.font(20), base.color("#ffffff"))
        return 100

    data = base.render_card(200, builder)
    _check_png(data)


# ---------------- 角色与武器图鉴 ----------------


async def test_character_encyclopedia(no_images):
    character = meta.get_character("芙宁娜", "gs")
    assert character is not None
    png = await encyclopedia.render_character_encyclopedia(character)
    _check_png(png)
    image = skia.Image.MakeFromEncoded(png)
    assert image is not None and image.height() > 2600


def test_encyclopedia_description_cleanup():
    text = encyclopedia._clean_text(["<h3>普通攻击</h3>", "造成伤害。", "<i>说明文字</i>"])
    assert text == "普通攻击\n造成伤害。\n说明文字"


async def test_weapon_encyclopedia(no_images):
    weapon = meta.get_weapon("雾切", "gs")
    assert weapon is not None
    assert encyclopedia.weapon_affix_text(weapon, 1).startswith("获得12%")
    png = await encyclopedia.render_weapon_encyclopedia(weapon)
    _check_png(png)


async def test_encyclopedia_indexes(no_images):
    _check_png(await encyclopedia.render_encyclopedia_index("character"))
    _check_png(await encyclopedia.render_encyclopedia_index("weapon"))


# ---------------- 抽卡详情 ----------------


async def test_gacha_detail_gs(data_dir, no_images):
    data = analyse.analyse_pool(1, GS_UID, "角色", "gs")
    assert data is not None
    face = {"name": "演示", "elem": "pyro"}
    png = await gacha_detail.render_gacha_detail(data, GS_UID, "gs", "角色活动祈愿", face=face)
    _check_png(png)


async def test_gacha_detail_sr(data_dir, no_images):
    data = analyse.analyse_pool(2, SR_UID, "光锥", "sr")
    assert data is not None
    png = await gacha_detail.render_gacha_detail(data, SR_UID, "sr", "光锥活动跃迁")
    _check_png(png)


async def test_gacha_detail_no_face(data_dir, no_images):
    data = analyse.analyse_pool(1, GS_UID, "常驻", "gs")
    assert data is not None
    png = await gacha_detail.render_gacha_detail(data, GS_UID, "gs", "常驻祈愿")
    _check_png(png)


# ---------------- 按版本统计 ----------------


async def test_gacha_stat_gs(data_dir, no_images):
    data = analyse.stat_pool(1, GS_UID, "全部", "gs")
    assert data is not None
    png = await gacha_stat.render_gacha_stat(data, GS_UID, "gs")
    _check_png(png)


async def test_gacha_stat_sr(data_dir, no_images):
    data = analyse.stat_pool(2, SR_UID, "角色", "sr")
    assert data is not None
    png = await gacha_stat.render_gacha_stat(data, SR_UID, "sr")
    _check_png(png)


# ---------------- 十连模拟抽卡 ----------------


async def test_gacha_trial(sim_data, no_images):
    rng = simulate._default_rng  # 系统随机即可，结构不受 rng 影响
    result = simulate.do_gacha("test:1", "role", is_master=True, rng=rng)
    assert result["code"] == "ok"
    png = await gacha_trial.render_gacha_trial(result, "测试玩家")
    _check_png(png)


async def test_gacha_trial_weapon(sim_data, no_images):
    result = simulate.do_gacha("test:2", "weapon", is_master=True)
    assert result["code"] == "ok"
    png = await gacha_trial.render_gacha_trial(result, "测试玩家")
    _check_png(png)


@pytest.mark.parametrize("game,count", [("sr", 10), ("zzz", 1), ("zzz", 10)])
async def test_multigame_gacha_images(sim_data, no_images, game, count):
    result = simulate.do_gacha("render:1", "role2", game=game, count=count, is_master=True)
    png = await gacha_trial.render_gacha_trial(result, "三游戏测试")
    _check_png(png)


# ---------------- 角色面板卡片 ----------------


def _avatars(game: str) -> dict:
    if game == "gs":
        raw = json.loads((AVATAR_FIXTURES / "enka_800055548.json").read_text(encoding="utf-8"))
        return parse_enka(raw, "800055548")["avatars"]
    raw = json.loads((AVATAR_FIXTURES / "mihomo_702762444.json").read_text(encoding="utf-8"))
    return parse_mihomo(raw, "702762444")["avatars"]


@pytest.mark.parametrize(("game", "avatar_id"), [("gs", "10000035"), ("sr", "1304")])
async def test_profile_damage_card_without_crit_rows(no_images, game, avatar_id):
    """七七（治疗）/ 砂金（护盾）的条目没有 dmg 字段，伤害卡仍须正常出图。"""
    avatar = _avatars(game)[avatar_id]
    attr = calc_attr(avatar, game)
    mark = calc_mark(avatar, attr, game)
    dmg = calc_dmg(avatar, game)
    assert any("dmg" not in row for row in dmg["dmgData"])
    png = await profile_detail.render_profile_detail(avatar, attr, mark, game, dmg)
    _check_png(png)
    assert skia.Image.MakeFromEncoded(png).height() > 600


@pytest.mark.parametrize(("game", "avatar_id"), [("gs", "10000030"), ("sr", "1211")])
async def test_profile_detail_with_custom_artis_rule(no_images, game, avatar_id):
    """钟离 / 白露走 artis.js 评分规则，详情卡须正常出图（评分不能抛错）。"""
    avatar = _avatars(game)[avatar_id]
    attr = calc_attr(avatar, game)
    mark = calc_mark(avatar, attr, game)
    assert mark["mark"] > 0
    _check_png(await profile_detail.render_profile_detail(avatar, attr, mark, game, None))


def test_dmg_row_text():
    assert profile_detail.dmg_row_text({"title": "战技护盾量", "avg": 488.4}) == "战技护盾量：488"
    assert (
        profile_detail.dmg_row_text({"title": "普攻伤害", "avg": 407.5, "dmg": 662.1})
        == "普攻伤害：期望 408 / 暴击 662"
    )
    assert profile_detail.dmg_row_text({"title": "测试", "avg": None, "dmg": 100}) == "测试：暴击 100"
    assert profile_detail.dmg_row_text({"title": "测试"}) == "测试：-"


async def test_profile_list_card(no_images):
    player = Player("800055548", "gs", {"name": "测试玩家", "avatars": _avatars("gs")})
    png = await profile_list.render_profile_list(player, "gs")
    _check_png(png)
    assert skia.Image.MakeFromEncoded(png).height() >= 430


async def test_artis_list_card(no_images):
    player = Player("800055548", "gs", {"name": "测试玩家", "avatars": _avatars("gs")})
    png = await artis_list.render_artis_list(player, "gs")
    _check_png(png)
    image = skia.Image.MakeFromEncoded(png)
    assert image.height() > 1000
    assert image.width() == 960


async def test_profile_detail_level_100(no_images):
    """私服 100 级角色也要能正常出详情卡。"""
    avatar = {**_avatars("gs")["10000051"], "level": 100}
    attr = calc_attr(avatar, "gs")
    mark = calc_mark(avatar, attr, "gs")
    dmg = calc_dmg(avatar, "gs")
    png = await profile_detail.render_profile_detail(avatar, attr, mark, "gs", dmg, uid="800055548")
    _check_png(png)
