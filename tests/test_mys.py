"""米游社面板数据源测试：DS 签名、gs/sr 面板映射、update_profile_mys 流程、命令注册

fixture 为手工构造的米游社原始响应（字段结构以 MysPanelData.js / MysPanelHSRData.js
读取的字段为准）：
- gs：character/detail 的 data 载荷（list[base/weapon/relics/skills/selected_properties]），
  角色用雷电将军（10000052，meta talentId 10521/10522/10525 → a/e/q），
  圣遗物 id 用 meta 里 400164 套装的 90540/90520/90550/90510/90530
- sr：avatar/info 的 data 载荷（avatar_list[id/level/rank/equip/skills/relics/ornaments/properties]），
  角色用白露（1211），遗器 61041（头）/ 63015（球）
"""
import re

import httpx
import pytest
import respx

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.core.attr_calc import calc_attr
from nonebot_plugin_miao.datasource import mys
from nonebot_plugin_miao.datasource.errors import ProfileError
from nonebot_plugin_miao.datasource.mys import (
    MysApi,
    get_ds,
    get_server,
    parse_gs_panel,
    parse_sr_panel,
    update_profile_mys,
)

GS_UID = "100000001"
SR_UID = "100000002"
GS_CHAR_LIST_URL = mys.GS_CHARACTER_LIST_URL
GS_CHAR_DETAIL_URL = mys.GS_CHARACTER_DETAIL_URL
SR_AVATAR_INFO_URL = mys.SR_AVATAR_INFO_URL

# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


def _gs_avatar_ds() -> dict:
    """雷电将军：cons3（q 展示 10 级，经命座扣减应为 7），5 件 5 星圣遗物"""
    return {
        "base": {
            "id": 10000052,
            "name": "雷电将军",
            "element": "雷",
            "level": 90,
            "fetter": 10,
            "actived_constellation_num": 3,
            "rarity": 5,
        },
        "weapon": {
            "id": 13509,
            "name": "薙草之稻光",
            "level": 90,
            "promote_level": 6,
            "affix_level": 1,
            "type": 11,
            "rarity": 5,
        },
        "relics": [
            {  # 花：主词条生命；副词条 hpPlus 239+298.75（times 1）、攻击% 4.7%（times 0）
                "id": 90540,
                "pos": 1,
                "level": 20,
                "rarity": 5,
                "main_property": {"property_type": 2, "name": "生命值", "value": "4780"},
                "sub_property_list": [
                    {"property_type": 2, "name": "生命值", "value": "537.75", "times": 1},
                    {"property_type": 6, "name": "攻击力百分比", "value": "4.7%", "times": 0},
                ],
            },
            {"id": 90520, "pos": 2, "level": 20, "rarity": 5,
             "main_property": {"property_type": 5, "name": "攻击力", "value": "311"},
             "sub_property_list": []},
            {"id": 90550, "pos": 3, "level": 20, "rarity": 5,
             "main_property": {"property_type": 23, "name": "元素充能效率", "value": "51.8%"},
             "sub_property_list": []},
            {"id": 90510, "pos": 4, "level": 20, "rarity": 5,
             "main_property": {"property_type": 41, "name": "雷元素伤害加成", "value": "46.6%"},
             "sub_property_list": []},
            {"id": 90530, "pos": 5, "level": 20, "rarity": 5,
             "main_property": {"property_type": 20, "name": "暴击率", "value": "31.1%"},
             "sub_property_list": []},
        ],
        "skills": [
            {"skill_id": 10521, "skill_type": 1, "level": 6, "name": "源流"},
            {"skill_id": 10522, "skill_type": 1, "level": 9, "name": "神变·恶曜开眼"},
            {"skill_id": 10525, "skill_type": 1, "level": 10, "name": "奥义·梦想真说"},
            {"skill_id": 10526, "skill_type": 2, "level": 1, "name": "被动"},  # 被动不进天赋
        ],
        "costumes": [],
        # 90 级不在突破边界，该字段不参与判定，仅保证结构完整
        "selected_properties": [
            {"property_type": 2000, "name": "生命值上限", "base": "12907", "final": "24907"},
        ],
    }


@pytest.fixture
def gs_detail_raw() -> dict:
    return {"list": [_gs_avatar_ds()]}


def _sr_avatar_ds() -> dict:
    """白露：rank1，光锥时节不居，遗器头（含速度副词条）+ 位面球"""
    return {
        "id": 1211,
        "name": "白露",
        "level": 80,
        "rank": 1,
        "rarity": 5,
        "equip": {"id": 23013, "name": "时节不居", "level": 80, "rank": 1, "rarity": 5},
        "relics": [
            {
                "id": 61041,
                "pos": 1,
                "level": 15,
                "rarity": 5,
                "main_property": {"property_type": 1, "name": "生命值", "value": "705.6"},
                "properties": [
                    {"property_type": 4, "name": "速度", "value": "2.0", "times": 1},
                    {"property_type": 1, "name": "生命值", "value": "67.7", "times": 1},
                ],
            },
        ],
        "ornaments": [
            {
                "id": 63015,
                "pos": 5,
                "level": 15,
                "rarity": 5,
                "main_property": {"property_type": 32, "name": "生命值百分比", "value": "43.2%"},
                "properties": [],
            },
        ],
        "skills": [
            {"point_id": 1211001, "point_type": 1, "level": 6, "remake": "普攻", "is_activated": True},
            {"point_id": 1211002, "point_type": 1, "level": 8, "remake": "战技", "is_activated": True},
            {"point_id": 1211003, "point_type": 1, "level": 8, "remake": "终结技", "is_activated": True},
            {"point_id": 1211004, "point_type": 1, "level": 8, "remake": "天赋", "is_activated": True},
            {"point_id": 1211007, "point_type": 1, "level": 1, "remake": "秘技", "is_activated": True},
            # 行迹节点：point_type 3 且已激活的进 trees
            {"point_id": 1211201, "point_type": 3, "level": 1, "is_activated": True},
            {"point_id": 1211202, "point_type": 3, "level": 1, "is_activated": True},
            {"point_id": 1211203, "point_type": 3, "level": 0, "is_activated": False},  # 未激活排除
            {"point_id": 1211006, "point_type": 2, "level": 1, "is_activated": True},  # type 2 排除
        ],
        # 速度 final 101 > 计算值 100（98 基础 + 2.0 速度词条），触发速度修正
        "properties": [
            {"property_type": 1, "name": "生命值", "base": "4000", "final": "5000"},
            {"property_type": 4, "name": "速度", "base": "98", "final": "101"},
        ],
        "servant_detail": {"servant_skills": []},
    }


@pytest.fixture
def sr_avatar_raw() -> dict:
    return {"avatar_list": [_sr_avatar_ds()]}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录重定向到临时目录"""
    d = tmp_path / "data"
    monkeypatch.setattr(store, "_data_dir", lambda: d)
    return d


# ---------------------------------------------------------------------------
# DS 签名（算法见 Miao-Yunzai mysApi.js getDs，期望值手工按算法计算）
# ---------------------------------------------------------------------------


def test_get_ds_fixed_vector_query():
    """固定 t/r + 排序 query：md5(salt&t&r&b=&q=role_id=...&server=...)"""
    ds = get_ds({"server": "cn_gf01", "role_id": "100000001"}, None, t=1700000000, r=123456)
    assert ds == "1700000000,123456,0cea3ef09388c60574ce981a0a397a3d"


def test_get_ds_fixed_vector_body():
    """固定 t/r + body（JSON.stringify 紧凑序列化）：q 为空"""
    ds = get_ds(None, {"role_id": "100000001", "server": "cn_gf01"}, t=1700000001, r=654321)
    assert ds == "1700000001,654321,6d08fbdc6e18aee017267ec935d4419c"


def test_get_ds_with_b_false():
    """with_b=False 时 body 不参与签名"""
    ds = get_ds(None, {"a": 1}, with_b=False, t=1700000001, r=654321)
    assert ds != "1700000001,654321,6d08fbdc6e18aee017267ec935d4419c"
    assert ds.startswith("1700000001,654321,")


def test_get_ds_random_format():
    """默认 t/r：格式 t,r,md5（r 为 6 位数字）"""
    ds = get_ds("a=1", None)
    assert re.fullmatch(r"\d{9,11},\d{6},[0-9a-f]{32}", ds)


def test_get_server():
    assert get_server("100000001", "gs") == "cn_gf01"
    assert get_server("500000001", "gs") == "cn_qd01"
    assert get_server("100000002", "sr") == "prod_gf_cn"
    assert get_server("500000002", "sr") == "prod_qd_cn"
    assert get_server("1800000001", "gs") == "os_asia"


# ---------------------------------------------------------------------------
# parse_gs_panel
# ---------------------------------------------------------------------------


def test_parse_gs_panel_basic(gs_detail_raw):
    """角色基础字段：id/名字/元素/等级/命座/好感，dataSource 为 mys"""
    player = parse_gs_panel(gs_detail_raw, GS_UID)
    assert player["uid"] == GS_UID
    assert player["dataSource"] == "mys"
    assert player["ttl"] == 60
    avatar = player["avatars"]["10000052"]
    assert avatar["id"] == 10000052
    assert avatar["name"] == "雷电将军"
    assert avatar["elem"] == "electro"  # "雷" 经元素表映射
    assert avatar["level"] == 90
    assert avatar["promote"] == 6  # 90 级 calc_promote 直接得 6
    assert avatar["cons"] == 3
    assert avatar["fetter"] == 10
    assert avatar["costume"] == 0
    assert avatar["_source"] == "mys"


def test_parse_gs_panel_talent(gs_detail_raw):
    """天赋：skill_id 经 meta talentId 映射 a/e/q，被动不进；cons3 时 q 展示等级 -3"""
    talent = parse_gs_panel(gs_detail_raw, GS_UID)["avatars"]["10000052"]["talent"]
    # talentCons q:3 命中（cons 3 >= 3）：q 10 - 3 = 7；e:5 未命中（3 < 5）保持 9
    assert talent == {"a": 6, "e": 9, "q": 7}


def test_parse_gs_panel_weapon(gs_detail_raw):
    """武器：id 经 meta 映射名字，promote_level/affix_level → promote/affix"""
    weapon = parse_gs_panel(gs_detail_raw, GS_UID)["avatars"]["10000052"]["weapon"]
    assert weapon == {"id": 13509, "name": "薙草之稻光", "level": 90, "promote": 6, "affix": 1}


def test_parse_gs_panel_artis(gs_detail_raw):
    """圣遗物：pos → 部位 1-5，mainId 查映射表，副词条反推 attrIds（次数+1 条）"""
    artis = parse_gs_panel(gs_detail_raw, GS_UID)["avatars"]["10000052"]["artis"]
    assert set(artis) == {"1", "2", "3", "4", "5"}
    slot1 = artis["1"]
    assert slot1["id"] == 90540
    assert slot1["level"] == 20
    assert slot1["star"] == 5
    assert slot1["mainId"] == 14001  # property_type 2 → 生命
    # hpPlus 537.75（times 1）→ 239+298.75 两条；攻击% 4.7%（times 0）→ 0.0466 一条
    assert slot1["attrIds"] == ["501022", "501024", "501062"]
    assert artis["3"]["mainId"] == 10007  # 充能沙
    assert artis["4"]["mainId"] == 15009  # 雷伤杯
    assert artis["5"]["mainId"] == 13007  # 暴击头


def test_parse_gs_panel_promote_boundary():
    """突破边界校正：80 级时用 selected_properties 的基础生命判断是否已突破"""
    ds = _gs_avatar_ds()
    ds["base"]["level"] = 80
    # 12000 恰为雷电将军 80 级已突破（promote 6）的基础生命
    ds["selected_properties"] = [{"property_type": 2000, "base": "12000", "final": "20000"}]
    avatar = parse_gs_panel({"list": [ds]}, GS_UID)["avatars"]["10000052"]
    assert avatar["promote"] == 6
    # 11388 为 80 级未突破（promote 5）的基础生命
    ds["selected_properties"] = [{"property_type": 2000, "base": "11388", "final": "20000"}]
    avatar = parse_gs_panel({"list": [ds]}, GS_UID)["avatars"]["10000052"]
    assert avatar["promote"] == 5


def test_parse_gs_panel_calc_attr(gs_detail_raw):
    """映射产物可直接喂给 calc_attr，hp/atk 为正"""
    avatar = parse_gs_panel(gs_detail_raw, GS_UID)["avatars"]["10000052"]
    attr = calc_attr(avatar, "gs")
    assert attr["hp"] > 0
    assert attr["atk"] > 0
    assert attr["dmg"] > 0  # 雷伤杯生效


def test_parse_gs_panel_empty():
    with pytest.raises(ProfileError, match="未返回角色数据"):
        parse_gs_panel({"list": []}, GS_UID)
    with pytest.raises(ProfileError, match="均无法识别"):
        parse_gs_panel({"list": [{"base": {"id": 99999999, "level": 1}}]}, GS_UID)


# ---------------------------------------------------------------------------
# parse_sr_panel
# ---------------------------------------------------------------------------


def test_parse_sr_panel_basic(sr_avatar_raw):
    """角色基础字段 + dataSource"""
    player = parse_sr_panel(sr_avatar_raw, SR_UID)
    assert player["uid"] == SR_UID
    assert player["dataSource"] == "mys"
    avatar = player["avatars"]["1211"]
    assert avatar["id"] == 1211
    assert avatar["name"] == "白露"
    assert avatar["level"] == 80
    assert avatar["promote"] == 6
    assert avatar["cons"] == 1
    assert avatar["_source"] == "mys"


def test_parse_sr_panel_talent_and_trees(sr_avatar_raw):
    """天赋按 remake 归 key；行迹取 point_type!=2 且已激活的节点并归一化"""
    avatar = parse_sr_panel(sr_avatar_raw, SR_UID)["avatars"]["1211"]
    assert avatar["talent"] == {"a": 6, "e": 8, "q": 8, "t": 8, "z": 1}
    # 1211201/1211202 进行迹（白露 meta tree 前缀即 1211），未激活与 point_type 2 的排除
    assert "1211201" in avatar["trees"]
    assert "1211202" in avatar["trees"]
    assert "1211203" not in avatar["trees"]
    assert all(str(t).startswith("1211") for t in avatar["trees"])


def test_parse_sr_panel_weapon(sr_avatar_raw):
    """光锥：equip id → meta 名字，rank → affix"""
    weapon = parse_sr_panel(sr_avatar_raw, SR_UID)["avatars"]["1211"]["weapon"]
    assert weapon == {"id": 23013, "name": "时节不居", "level": 80, "promote": 6, "affix": 1}


def test_parse_sr_panel_artis(sr_avatar_raw):
    """遗器/饰品合并：pos 1-6；mainId 由 meta mainIdx 反查；副词条 "id,times,step" 格式"""
    artis = parse_sr_panel(sr_avatar_raw, SR_UID)["avatars"]["1211"]["artis"]
    assert set(artis) == {"1", "5"}
    slot1 = artis["1"]
    assert slot1["id"] == 61041
    assert slot1["level"] == 15
    assert slot1["mainId"] == 1  # property_type 1 → hpPlus → mainIdx[1] 的 id 1
    # 速度 2.0（times 1）：base 2.0 → step 0 → "7,1,0"，经速度修正后步数 +4（差额 1.0/0.3）
    assert slot1["attrIds"][0] == "7,1,4"
    # hpPlus 67.7（times 1）：(67.7-33.87)/4.234 ≈ 8 → "1,1,8"
    assert slot1["attrIds"][1] == "1,1,8"
    assert artis["5"]["id"] == 63015
    assert artis["5"]["mainId"] == 1  # property_type 32 → hp 百分比 → mainIdx[5] 的 id 1


def test_parse_sr_panel_speed_no_fix_when_aligned():
    """速度 final 与计算值一致时不触发修正（速度词条步数保持 0）"""
    ds = _sr_avatar_ds()
    ds["properties"][1]["final"] = "100"
    artis = parse_sr_panel({"avatar_list": [ds]}, SR_UID)["avatars"]["1211"]["artis"]
    assert artis["1"]["attrIds"][0] == "7,1,0"


def test_parse_sr_panel_enhanced_remap():
    """加强角色：skills 含 "1{原id}" 前缀 point_id 时归入 2 开头的新 id"""
    ds = _sr_avatar_ds()
    ds["id"] = 1212  # 镜流（ENHANCED_CHAR_IDS 之一）
    ds["skills"] = [
        {"point_id": 11212001, "point_type": 1, "level": 6, "remake": "普攻", "is_activated": True},
        {"point_id": 11212201, "point_type": 3, "level": 1, "is_activated": True},
    ]
    avatar = parse_sr_panel({"avatar_list": [ds]}, SR_UID)["avatars"]["2212"]
    assert avatar["id"] == 2212
    assert avatar["name"] == "镜流Pro"
    # point_id 前缀同步重映射
    assert "12212201" in avatar["trees"]


def test_parse_sr_panel_calc_attr(sr_avatar_raw):
    """映射产物可直接喂给 calc_attr，hp/atk/speed 为正"""
    avatar = parse_sr_panel(sr_avatar_raw, SR_UID)["avatars"]["1211"]
    attr = calc_attr(avatar, "sr")
    assert attr["hp"] > 0
    assert attr["atk"] > 0
    assert attr["speed"] > 0


def test_parse_sr_panel_empty():
    with pytest.raises(ProfileError, match="未返回角色数据"):
        parse_sr_panel({"avatar_list": []}, SR_UID)


# ---------------------------------------------------------------------------
# MysApi 请求（respx mock）
# ---------------------------------------------------------------------------


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"retcode": 0, "message": "OK", "data": data})


async def test_mys_api_gs_flow(gs_detail_raw):
    """get_character_ids + gs_panel：POST body 带 role_id/server/character_ids，头带 DS/Cookie"""
    with respx.mock(assert_all_called=True) as router:
        list_route = router.post(GS_CHAR_LIST_URL).mock(return_value=_ok({"list": [{"id": 10000052}]}))
        detail_route = router.post(GS_CHAR_DETAIL_URL).mock(return_value=_ok(gs_detail_raw))
        api = MysApi("accountid=1; ltoken=abc", "gs")
        try:
            ids = await api.get_character_ids(GS_UID)
            assert ids == [10000052]
            data = await api.gs_panel(GS_UID, ids)
            assert data["list"][0]["base"]["id"] == 10000052
        finally:
            await api.aclose()
    # 请求头与 body 校验
    req = list_route.calls[0].request
    assert req.headers["cookie"] == "accountid=1; ltoken=abc"
    assert re.fullmatch(r"\d{9,11},\d{6},[0-9a-f]{32}", req.headers["ds"])
    assert req.headers["x-rpc-app_version"] == "2.40.1"
    assert req.headers["x-rpc-client_type"] == "5"
    assert "miHoYoBBS/2.40.1" in req.headers["user-agent"]
    import json as _json

    assert _json.loads(req.content) == {"role_id": GS_UID, "server": "cn_gf01"}
    body = _json.loads(detail_route.calls[0].request.content)
    assert body["character_ids"] == [10000052]


async def test_mys_api_sr_flow(sr_avatar_raw):
    """sr_panel：GET 带 need_wiki/role_id/server 查询参数"""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SR_AVATAR_INFO_URL).mock(return_value=_ok(sr_avatar_raw))
        api = MysApi("ck", "sr")
        try:
            data = await api.sr_panel(SR_UID)
            assert data["avatar_list"][0]["id"] == 1211
        finally:
            await api.aclose()
    url = route.calls[0].request.url
    assert url.params["role_id"] == SR_UID
    assert url.params["server"] == "prod_gf_cn"
    assert url.params["need_wiki"] == "true"


async def test_mys_api_retcode_cookie_invalid():
    """retcode 10104 → cookie 无效提示"""
    with respx.mock(assert_all_called=True) as router:
        router.post(GS_CHAR_LIST_URL).mock(
            return_value=httpx.Response(200, json={"retcode": 10104, "message": "cookie无效", "data": None})
        )
        api = MysApi("bad_ck", "gs")
        try:
            with pytest.raises(ProfileError, match="cookie 无效或已过期"):
                await api.get_character_ids(GS_UID)
        finally:
            await api.aclose()


async def test_mys_api_retcode_other():
    """retcode -1 等 → 带 message 的 ProfileError"""
    with respx.mock(assert_all_called=True) as router:
        router.get(SR_AVATAR_INFO_URL).mock(
            return_value=httpx.Response(200, json={"retcode": -1, "message": "系统繁忙", "data": None})
        )
        api = MysApi("ck", "sr")
        try:
            with pytest.raises(ProfileError, match="系统繁忙"):
                await api.sr_panel(SR_UID)
        finally:
            await api.aclose()


async def test_mys_api_oversea_uid():
    """国际服 UID 直接报错（不发请求）"""
    api = MysApi("ck", "gs")
    try:
        with pytest.raises(ProfileError, match="国服"):
            await api.get_character_ids("800055548")
    finally:
        await api.aclose()


# ---------------------------------------------------------------------------
# update_profile_mys
# ---------------------------------------------------------------------------


async def test_update_profile_mys_no_cookie(data_dir):
    """无 cookie（用户未绑定、全局未配置）→ no_cookie，不发起请求"""
    with respx.mock(assert_all_called=False):
        ret = await update_profile_mys(12345, GS_UID, "gs")
    assert ret["code"] == "no_cookie"
    assert "绑定cookie" in ret["msg"]


async def test_update_profile_mys_gs_ok_and_cd(data_dir, gs_detail_raw, monkeypatch):
    """原神全流程：用户 cookie 优先 → 抓取解析落盘 → CD 内第二次拦截"""
    monkeypatch.setattr(mys, "_global_cookie", lambda: "global_ck")
    store.set_cookie(12345, "user_ck")
    with respx.mock(assert_all_called=False) as router:
        list_route = router.post(GS_CHAR_LIST_URL).mock(return_value=_ok({"list": [{"id": 10000052}]}))
        router.post(GS_CHAR_DETAIL_URL).mock(return_value=_ok(gs_detail_raw))

        ret = await update_profile_mys(12345, GS_UID, "gs")
        assert ret["code"] == "ok"
        assert ret["new_chars"] == ["雷电将军"]

        # 落盘验证
        saved = store.read_player("gs", GS_UID)
        assert saved["dataSource"] == "mys"
        assert saved["avatars"]["10000052"]["weapon"]["name"] == "薙草之稻光"

        # 用户 cookie 优先于全局
        assert list_route.calls[0].request.headers["cookie"] == "user_ck"

        # CD 内第二次：不再请求
        ret2 = await update_profile_mys(12345, GS_UID, "gs")
        assert ret2["code"] == "cd"
        assert ret2["wait"] > 0
        assert list_route.call_count == 1


async def test_update_profile_mys_global_cookie_fallback(data_dir, gs_detail_raw, monkeypatch):
    """用户未绑定 cookie 时回退全局 cookie"""
    monkeypatch.setattr(mys, "_global_cookie", lambda: "global_ck")
    with respx.mock(assert_all_called=False) as router:
        list_route = router.post(GS_CHAR_LIST_URL).mock(return_value=_ok({"list": [{"id": 10000052}]}))
        router.post(GS_CHAR_DETAIL_URL).mock(return_value=_ok(gs_detail_raw))
        ret = await update_profile_mys(12345, GS_UID, "gs")
    assert ret["code"] == "ok"
    assert list_route.calls[0].request.headers["cookie"] == "global_ck"


async def test_update_profile_mys_sr_ok(data_dir, sr_avatar_raw):
    """星铁全流程：sr_panel → 落盘"""
    store.set_cookie(12345, "user_ck")
    with respx.mock(assert_all_called=False) as router:
        router.get(SR_AVATAR_INFO_URL).mock(return_value=_ok(sr_avatar_raw))
        ret = await update_profile_mys(12345, SR_UID, "sr")
    assert ret["code"] == "ok"
    assert ret["new_chars"] == ["白露"]
    saved = store.read_player("sr", SR_UID)
    assert saved["dataSource"] == "mys"
    assert saved["avatars"]["1211"]["talent"]["q"] == 8


async def test_update_profile_mys_error_not_cached(data_dir):
    """cookie 失效（10104）抛 ProfileError，不写 CD 不落盘"""
    store.set_cookie(12345, "bad_ck")
    with respx.mock(assert_all_called=False) as router:
        router.post(GS_CHAR_LIST_URL).mock(
            return_value=httpx.Response(200, json={"retcode": 10104, "message": "cookie无效", "data": None})
        )
        with pytest.raises(ProfileError, match="cookie 无效或已过期"):
            await update_profile_mys(12345, GS_UID, "gs")
    assert store.check_cd(f"profile:mys:gs:{GS_UID}", 180) == 0
    assert store.read_player("gs", GS_UID) == {}


# ---------------------------------------------------------------------------
# 命令注册冒烟
# ---------------------------------------------------------------------------


@pytest.fixture
def cmds():
    """在 nonebot 初始化后导入 commands 模块（on_regex 注册要求已初始化）"""
    from nonebot_plugin_miao.commands import profile

    return profile


def test_mys_update_matcher_registered(cmds):
    from nonebot.matcher import matchers

    all_matchers = set()
    for ms in matchers.values():
        all_matchers.update(ms)
    assert cmds.mys_update_m in all_matchers


def test_mys_update_regex(cmds):
    for text in ("/米游社更新面板", "/米游社面板更新", "/星铁米游社更新面板",
                 "/原神mys面板更新", "/mys更新面板", "/米游社更新面板 800055548"):
        assert re.match(cmds.RE_MYS_UPDATE, text), text
    for text in ("米游社更新面板", "#米游社更新面板", "/更新面板", "/米游社面板", "/面板列表"):
        assert not re.match(cmds.RE_MYS_UPDATE, text), text
    # 不误伤/不被误伤：普通更新面板不命中 mys 正则，mys 指令不命中普通更新与角色面板正则
    assert not re.match(cmds.RE_UPDATE, "/米游社更新面板")
    assert not re.match(cmds.RE_DETAIL, "/米游社更新面板")
    assert not re.match(cmds.RE_DETAIL, "/星铁mys面板更新")
    assert not re.match(cmds.RE_DETAIL, "/米游社面板更新 800055548")
