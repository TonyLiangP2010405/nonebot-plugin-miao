"""面板数据源测试：enka（原神）/ mihomo（星铁）解析、update_profile 流程、Player 存取

fixture 为真实录制的 API 响应（2026-07 录制，见 tests/fixtures/）：
- enka_800055548.json：enka.network/api/uid/800055548，展示柜 8 个角色
- mihomo_702762444.json：api.mihomo.me/sr_info/702762444，含加强角色（enhancedId=1）
HTTP 请求用 respx 拦截；store._data_dir monkeypatch 到 tmp_path。
"""
import json
from pathlib import Path

import httpx
import pytest
import respx

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.core.player import Player
from nonebot_plugin_miao.datasource.enka import fetch_enka, parse_enka
from nonebot_plugin_miao.datasource.errors import ProfileError
from nonebot_plugin_miao.datasource.mihomo import fetch_mihomo, parse_mihomo
from nonebot_plugin_miao.datasource.profile_service import update_profile

FIXTURES = Path(__file__).parent / "fixtures"
ENKA_UID = "800055548"
MIHOMO_UID = "702762444"
ENKA_URL = f"https://enka.network/api/uid/{ENKA_UID}"
MIHOMO_URL = f"https://api.mihomo.me/sr_info/{MIHOMO_UID}"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def enka_raw() -> dict:
    return _load_fixture("enka_800055548.json")


@pytest.fixture
def mihomo_raw() -> dict:
    return _load_fixture("mihomo_702762444.json")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录重定向到临时目录"""
    d = tmp_path / "data"
    monkeypatch.setattr(store, "_data_dir", lambda: d)
    return d


# ---------------- parse_enka ----------------


def test_parse_enka_basic(enka_raw):
    """玩家基础字段：name/level/word/face/sign/dataSource"""
    player = parse_enka(enka_raw, ENKA_UID)
    assert player["uid"] == ENKA_UID
    assert player["name"] == "ゴキちゃん"
    assert player["level"] == 55
    assert player["word"] == 8
    assert player["face"] == 10000060  # profilePicture.avatarId
    assert player["sign"] == "ジャッジメントですの!!!"
    assert player["dataSource"] == "enka"
    assert player["ttl"] == 34
    assert player["updateTime"] > 0
    # 展示柜 8 个角色全部解析（id 与 meta 一致）
    assert set(player["avatars"]) == {
        "10000051", "10000016", "10000030", "10000033",
        "10000029", "10000042", "10000035", "10000052",
    }


def test_parse_enka_avatar_fields(enka_raw):
    """逐字段断言：优菈（fixture 首个角色）的 level/promote/cons/fetter/talent"""
    avatar = parse_enka(enka_raw, ENKA_UID)["avatars"]["10000051"]
    assert avatar["id"] == 10000051
    assert avatar["name"] == "优菈"
    assert avatar["elem"] == "cryo"  # talentElem 缺失时回退 meta elem
    assert avatar["level"] == 85  # propMap['4001'].val
    assert avatar["promote"] == 6  # propMap['1002'].val
    assert avatar["cons"] == 0  # fixture 无 talentIdList
    assert avatar["fetter"] == 10  # fetterInfo.expLevel
    assert avatar["costume"] == 0
    assert avatar["_source"] == "enka"
    # skillLevelMap {'10511': 8, '10512': 5, '10515': 6} 经 meta talentId 映射为 a/e/q
    assert avatar["talent"] == {"a": 8, "e": 5, "q": 6}


def test_parse_enka_weapon(enka_raw):
    """武器：equipList 中 ITEM_WEAPON 项，itemId 经 meta 映射为武器名，affix = affixMap 值 + 1"""
    weapon = parse_enka(enka_raw, ENKA_UID)["avatars"]["10000051"]["weapon"]
    assert weapon == {
        "id": 12406,
        "name": "试作古华",
        "level": 90,
        "promote": 6,
        "affix": 3,  # affixMap {'112406': 2} + 1
    }


def test_parse_enka_artis(enka_raw):
    """圣遗物：equipType → 部位 1-5，level = reliquary.level - 1，mainId/attrIds 透传"""
    artis = parse_enka(enka_raw, ENKA_UID)["avatars"]["10000051"]["artis"]
    assert set(artis) == {"1", "2", "3", "4", "5"}
    slot1 = artis["1"]  # EQUIP_BRACER → 花
    assert slot1["id"] == 92543
    assert slot1["level"] == 20  # reliquary.level 21 - 1
    assert slot1["star"] == 5
    assert slot1["mainId"] == 14001
    assert slot1["attrIds"] == [501062, 501081, 501224, 501051, 501221, 501051, 501084, 501221]


def test_parse_enka_unknown_char_skipped(enka_raw):
    """meta 查不到的角色跳过，其余角色正常解析"""
    raw = json.loads(json.dumps(enka_raw))
    raw["avatarInfoList"][0]["avatarId"] = 99999999
    player = parse_enka(raw, ENKA_UID)
    assert "99999999" not in player["avatars"]
    assert len(player["avatars"]) == 7


def test_parse_enka_empty_showcase():
    """展示柜为空（无 avatarInfoList）抛 ProfileError"""
    with pytest.raises(ProfileError, match="展示柜为空"):
        parse_enka({"playerInfo": {"nickname": "x"}, "ttl": 60}, "800000001")
    # avatarInfoList 首项无 propMap 同样视为空（对齐 EnkaApi.js response）
    with pytest.raises(ProfileError, match="展示柜为空"):
        parse_enka({"playerInfo": {"nickname": "x"}, "avatarInfoList": [{}]}, "800000001")


# ---------------- parse_mihomo ----------------


def test_parse_mihomo_basic(mihomo_raw):
    """玩家基础字段 + assist/展示柜角色合并 + 加强角色 id 重映射"""
    player = parse_mihomo(mihomo_raw, MIHOMO_UID)
    assert player["uid"] == MIHOMO_UID
    assert player["name"] == "sup2ch"
    assert player["level"] == 67
    assert player["dataSource"] == "mihomo"
    # avatarDetailList 5 个 + assistAvatarList 独有的白露 1211
    # 其中 1212/1004/1005/1006 为加强角色（enhancedId=1），归入 2212/2004/2005/2006
    assert set(player["avatars"]) == {"2212", "1304", "2004", "2005", "2006", "1211"}


def test_parse_mihomo_avatar_fields(mihomo_raw):
    """逐字段断言：镜流Pro（加强角色 1212→2212）的 level/promote/cons/talent/trees"""
    avatar = parse_mihomo(mihomo_raw, MIHOMO_UID)["avatars"]["2212"]
    assert avatar["id"] == 2212
    assert avatar["name"] == "镜流Pro"
    assert avatar["level"] == 80
    assert avatar["promote"] == 6  # promotion
    assert avatar["cons"] == 0  # fixture 无 rank 字段
    assert avatar["_source"] == "mihomo"
    # pointId '11212xxx' 重映射为 '2212xxx' 后按后缀映射天赋：001→a 002→e 003→q 004→t 007→z
    assert avatar["talent"] == {"a": 6, "e": 10, "q": 10, "t": 10, "z": 1}
    # level 为 1 的行迹节点进 trees，并按 meta tree 归一化（对齐 Avatar.js setTrees）：
    # 201-210 → meta tree key '12212xxx'，101-103 → 前缀补 '{prefix}10x' 即 '2212xxx'
    assert len(avatar["trees"]) == 13
    assert "12212201" in avatar["trees"]
    assert "2212101" in avatar["trees"]
    assert all(t.startswith("12212") or t.startswith("221210") for t in avatar["trees"])


def test_parse_mihomo_weapon_and_artis(mihomo_raw):
    """光锥与遗器：equipment tid → meta 武器名；relicList type 为部位，attrIds 为 affixId,cnt,step"""
    avatar = parse_mihomo(mihomo_raw, MIHOMO_UID)["avatars"]["2212"]
    assert avatar["weapon"] == {
        "id": 23014,
        "name": "此身为剑",
        "level": 80,
        "promote": 6,
        "affix": 1,  # rank
    }
    artis = avatar["artis"]
    assert set(artis) == {"1", "2", "3", "4", "5", "6"}
    slot1 = artis["1"]
    assert slot1["id"] == 61041
    assert slot1["level"] == 15
    assert slot1["mainId"] == 1
    # subAffixList：无 step 的补 0（对齐 HomoData.getArtis）
    assert slot1["attrIds"] == ["2,3,3", "5,1,0", "8,1,2", "9,3,1"]


def test_parse_mihomo_assist_avatar(mihomo_raw):
    """仅在 assistAvatarList 出现的白露也解析（天赋按 id 后缀映射）"""
    avatar = parse_mihomo(mihomo_raw, MIHOMO_UID)["avatars"]["1211"]
    assert avatar["name"] == "白露"
    assert avatar["level"] == 80
    assert avatar["talent"]["a"] == 6  # pointId 1211001 → 后缀 001 → a
    assert avatar["weapon"]["id"] == 23013


def test_parse_mihomo_empty_showcase():
    """展示柜为空抛 ProfileError"""
    with pytest.raises(ProfileError, match="展示柜为空"):
        parse_mihomo({"detailInfo": {"nickname": "x"}}, "800000001")


# ---------------- fetch（respx mock） ----------------


async def test_fetch_enka_ok(enka_raw):
    with respx.mock(assert_all_called=False) as router:
        router.get(ENKA_URL).mock(return_value=httpx.Response(200, json=enka_raw))
        data = await fetch_enka(ENKA_UID)
    assert data["playerInfo"]["nickname"] == "ゴキちゃん"


async def test_fetch_enka_404():
    with respx.mock(assert_all_called=False) as router:
        router.get(ENKA_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(ProfileError, match="404"):
            await fetch_enka(ENKA_UID)


async def test_fetch_enka_no_player_info():
    """200 但无 playerInfo（服务异常）抛 ProfileError"""
    with respx.mock(assert_all_called=False) as router:
        router.get(ENKA_URL).mock(return_value=httpx.Response(200, json={"error": "Maintenance"}))
        with pytest.raises(ProfileError, match="未返回玩家数据"):
            await fetch_enka(ENKA_UID)


async def test_fetch_mihomo_404():
    with respx.mock(assert_all_called=False) as router:
        router.get(MIHOMO_URL).mock(return_value=httpx.Response(404, json={"detail": "User not found"}))
        with pytest.raises(ProfileError, match="404"):
            await fetch_mihomo(MIHOMO_UID)


# ---------------- update_profile ----------------


async def test_update_profile_ok_and_cd(data_dir, enka_raw):
    """首次更新成功落盘，CD 内第二次直接拦截不再请求"""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(ENKA_URL).mock(return_value=httpx.Response(200, json=enka_raw))

        ret = await update_profile(12345, ENKA_UID, "gs")
        assert ret["code"] == "ok"
        assert "优菈" in ret["new_chars"]
        assert len(ret["new_chars"]) == 8
        player = ret["player"]
        assert player.name == "ゴキちゃん"

        # 落盘验证
        saved = store.read_player("gs", ENKA_UID)
        assert saved["dataSource"] == "enka"
        assert saved["avatars"]["10000051"]["weapon"]["name"] == "试作古华"

        # CD 内第二次调用：不发起 HTTP 请求
        ret2 = await update_profile(12345, ENKA_UID, "gs")
        assert ret2["code"] == "cd"
        assert ret2["wait"] > 0
        assert route.call_count == 1

        # CD 持续到配置间隔（3 分钟），不受 ttl=34 缩短
        assert ret2["wait"] > 60


async def test_update_profile_mihomo(data_dir, mihomo_raw):
    """星铁走 mihomo 数据源，落盘后 Player 可按别名查询"""
    with respx.mock(assert_all_called=False) as router:
        router.get(MIHOMO_URL).mock(return_value=httpx.Response(200, json=mihomo_raw))
        ret = await update_profile(12345, MIHOMO_UID, "sr")
    assert ret["code"] == "ok"
    assert "镜流Pro" in ret["new_chars"]
    player = Player.load(MIHOMO_UID, "sr")
    assert player.get_avatar("镜流Pro")["level"] == 80


async def test_update_profile_error_not_cached(data_dir):
    """数据源报错时不写 CD（允许立即重试）、不落盘"""
    with respx.mock(assert_all_called=False) as router:
        router.get(ENKA_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(ProfileError, match="404"):
            await update_profile(12345, ENKA_UID, "gs")
    assert store.check_cd(f"profile:gs:{ENKA_UID}", 180) == 0
    assert store.read_player("gs", ENKA_UID) == {}


async def test_update_profile_merge_keeps_other_avatars(data_dir, enka_raw):
    """合并更新：本次未涉及的角色保留原数据"""
    player = Player.load(ENKA_UID, "gs")
    player.update({
        "name": "旧数据",
        "avatars": {"10000002": {"id": 10000002, "name": "神里绫华", "level": 90, "_source": "enka"}},
        "dataSource": "enka",
        "updateTime": 1,
    })
    player.save()
    with respx.mock(assert_all_called=False) as router:
        router.get(ENKA_URL).mock(return_value=httpx.Response(200, json=enka_raw))
        ret = await update_profile(12345, ENKA_UID, "gs")
    assert ret["code"] == "ok"
    merged = ret["player"]
    # 旧角色保留，新角色覆盖/新增，基础信息被覆盖
    assert merged.get_avatar("神里绫华")["level"] == 90
    assert merged.get_avatar("优菈")["level"] == 85
    assert merged.name == "ゴキちゃん"


# ---------------- Player 存取往返 ----------------


def test_player_roundtrip(data_dir):
    """save → load 往返一致，get_avatar 支持别名，avatar_names 解析为角色名"""
    player = Player.load("100000001", "gs")
    assert player.avatars == {}
    player.update({
        "name": "测试",
        "level": 60,
        "sign": "签名",
        "avatars": {
            "10000052": {
                "id": 10000052, "name": "雷电将军", "elem": "electro",
                "level": 90, "promote": 6, "cons": 2, "fetter": 10,
                "talent": {"a": 6, "e": 9, "q": 10},
                "weapon": {"id": 13509, "name": "薙草之稻光", "level": 90, "promote": 6, "affix": 1},
                "artis": {}, "_source": "enka", "_time": 1,
            },
        },
        "dataSource": "enka",
        "updateTime": 100,
    })
    player.save()

    loaded = Player.load("100000001", "gs")
    assert loaded.name == "测试"
    assert loaded.data_source == "enka"
    assert loaded.update_time == 100
    # 名字与别名都能取到（雷电将军别名见 meta alias.js，如雷电影/雷神）
    assert loaded.get_avatar("雷电将军")["cons"] == 2
    assert loaded.get_avatar("10000052")["talent"]["q"] == 10
    assert loaded.avatar_names() == ["雷电将军"]
    assert loaded.get_avatar("不存在的角色") is None

    # 星铁存储隔离
    assert Player.load("100000001", "sr").avatars == {}
