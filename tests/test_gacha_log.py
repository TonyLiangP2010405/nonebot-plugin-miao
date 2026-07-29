"""P2 抽卡记录流水线测试：authkey 解析、分页抓取、增量更新、错误码、UIGF 导入导出、缓存

HTTP 请求用 respx 在传输层拦截；store._data_dir monkeypatch 到 tmp_path，不污染真实数据目录。
"""
import asyncio
import types

import httpx
import pytest
import respx

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.datasource import gacha_log, uigf
from nonebot_plugin_miao.datasource.gacha_log import GachaLogError

GS_URL = "https://public-operation-hk4e.mihoyo.com/gacha_info/api/getGachaLog"
UID = "100000001"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录重定向到临时目录"""
    d = tmp_path / "data"
    monkeypatch.setattr(store, "_data_dir", lambda: d)
    return d


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """把 asyncio.sleep 替换为空操作，避免分页/池间延迟拖慢测试"""
    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)


@pytest.fixture
def authkey_info():
    return {"authkey": "testkey/=", "region": "cn_gf01", "game": "gs"}


def make_records(ids, gacha_type=301):
    """构造 API 原始字段的抽卡记录（id 列表，最新在前）"""
    return [
        {
            "uid": UID,
            "gacha_type": str(gacha_type),
            "item_id": "",
            "count": "1",
            "time": "2024-01-01 00:00:00",
            "name": f"物品{i}",
            "lang": "zh-cn",
            "item_type": "角色",
            "rank_type": "3",
            "id": str(i),
        }
        for i in ids
    ]


def api_ok(records):
    return httpx.Response(
        200,
        json={"retcode": 0, "message": "OK", "data": {"page": "1", "size": "20", "list": records, "region": "cn_gf01"}},
    )


# ---------------- parse_authkey_url ----------------


def test_parse_authkey_url_gs():
    text = (
        "https://public-operation-hk4e.mihoyo.com/gacha_info/api/getGachaLog?"
        "authkey_ver=1&sign_type=2&auth_appid=webview_gacha&init_type=301&gacha_id=abc"
        "&timestamp=1641338980&lang=zh-cn&device_type=mobile&game_version=CNRELiOS3.0.0_R10283122_S10475436_D10616957"
        "&region=cn_gf01&authkey=abc%2Fdef%2Bghi%3D&game_biz=hk4e_cn&gacha_type=301&page=1&size=5&end_id=0"
    )
    info = gacha_log.parse_authkey_url(text)
    assert info["authkey"] == "abc/def+ghi="  # URL 编码被还原
    assert info["region"] == "cn_gf01"
    assert info["game"] == "gs"


def test_parse_authkey_url_sr():
    text = (
        "https://public-operation-hkrpg.mihoyo.com/common/gacha_record/api/getGachaLog?"
        "authkey_ver=1&authkey=xyz123&game_biz=hkrpg_cn&region=prod_gf_cn&gacha_type=11&page=1&size=20&end_id=0"
    )
    info = gacha_log.parse_authkey_url(text)
    assert info["authkey"] == "xyz123"
    assert info["region"] == "prod_gf_cn"
    assert info["game"] == "sr"


def test_parse_authkey_url_in_long_text():
    text = (
        "帮我更新一下抽卡记录 https://webstatic.mihoyo.com/hk4e/event/e20190909gacha-v3/index.html?"
        "authkey_ver=1&authkey=long%2Ftext%3D%3D&region=cn_qd01&init_type=301#/log 谢谢啦"
    )
    info = gacha_log.parse_authkey_url(text)
    assert info["authkey"] == "long/text=="
    assert info["region"] == "cn_qd01"
    assert info["game"] == "gs"


def test_parse_authkey_url_no_region():
    text = "https://public-operation-hk4e.mihoyo.com/gacha_info/api/getGachaLog?authkey_ver=1&authkey=abc&lang=zh-cn"
    info = gacha_log.parse_authkey_url(text)
    assert info["region"] is None


def test_parse_authkey_url_invalid():
    with pytest.raises(GachaLogError, match="链接不完整"):
        gacha_log.parse_authkey_url("随便发一段文字，根本没有链接")
    with pytest.raises(GachaLogError):
        gacha_log.parse_authkey_url("https://example.com/foo?a=1&b=2")
    with pytest.raises(GachaLogError):
        gacha_log.parse_authkey_url("")


# ---------------- region / 服务器判定 ----------------


def test_region_from_uid():
    assert gacha_log.region_from_uid("100000001", "gs") == "cn_gf01"  # 官服
    assert gacha_log.region_from_uid("200000001", "gs") == "cn_gf01"
    assert gacha_log.region_from_uid("500000001", "gs") == "cn_qd01"  # B服
    assert gacha_log.region_from_uid("600000001", "gs") == "os_usa"  # 美服
    assert gacha_log.region_from_uid("700000001", "gs") == "os_euro"
    assert gacha_log.region_from_uid("800000001", "gs") == "os_asia"
    assert gacha_log.region_from_uid("1800000000", "gs") == "os_asia"  # 亚服 18 开头
    assert gacha_log.region_from_uid("900000001", "gs") == "os_cht"  # 港澳台
    assert gacha_log.region_from_uid("100000001", "sr") == "prod_gf_cn"
    assert gacha_log.region_from_uid("800000001", "sr") == "prod_official_asia"


# ---------------- 分页抓取 ----------------


async def test_fetch_gacha_log_pagination(authkey_info):
    """两页数据（20 + 3）正确拼接，第二页请求带上 page=2 和上一页末尾的 end_id"""
    page1 = make_records(range(100, 80, -1))  # 100..81
    page2 = make_records(range(80, 77, -1))  # 80..78
    calls = []

    def handler(request: httpx.Request):
        params = dict(request.url.params)
        calls.append(params)
        return api_ok(page1 if params["page"] == "1" else page2)

    with respx.mock(assert_all_called=False) as router:
        router.get(GS_URL).mock(side_effect=handler)
        logs = await gacha_log.fetch_gacha_log(authkey_info, 301)

    assert [r["id"] for r in logs] == [str(i) for i in range(100, 77, -1)]
    assert len(calls) == 2
    assert calls[0]["end_id"] == "0"
    assert calls[1]["page"] == "2"
    assert calls[1]["end_id"] == "81"  # 第一页最后一条的 id
    assert calls[0]["size"] == "20"
    assert calls[0]["authkey"] == "testkey/="
    assert calls[0]["region"] == "cn_gf01"


async def test_fetch_gacha_log_empty(authkey_info):
    with respx.mock(assert_all_called=False) as router:
        router.get(GS_URL).mock(return_value=api_ok([]))
        assert await gacha_log.fetch_gacha_log(authkey_info, 301) == []


async def test_fetch_gacha_log_error_101(authkey_info):
    with respx.mock(assert_all_called=False) as router:
        router.get(GS_URL).mock(return_value=httpx.Response(200, json={"retcode": -101, "message": "authkey error"}))
        with pytest.raises(GachaLogError, match="过期"):
            await gacha_log.fetch_gacha_log(authkey_info, 301)


async def test_fetch_gacha_log_error_100_and_109(authkey_info):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(GS_URL)
        route.mock(return_value=httpx.Response(200, json={"retcode": -100, "message": "authkey error"}))
        with pytest.raises(GachaLogError, match="链接不完整"):
            await gacha_log.fetch_gacha_log(authkey_info, 301)
        route.mock(return_value=httpx.Response(200, json={"retcode": -109, "message": "forbidden"}))
        with pytest.raises(GachaLogError, match="反馈的链接已无法查询"):
            await gacha_log.fetch_gacha_log(authkey_info, 301)


async def test_fetch_gacha_log_region_from_uid():
    """authkey_info 缺 region 时按 UID 推断：8 开头走国际服域名"""
    info = {"authkey": "k", "region": None, "game": "gs"}
    os_url = "https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/getGachaLog"
    with respx.mock(assert_all_called=False) as router:
        route = router.get(os_url).mock(return_value=api_ok([]))
        await gacha_log.fetch_gacha_log(info, 301, uid="800000001")
        assert route.called
        params = dict(route.calls[0].request.url.params)
        assert params["region"] == "os_asia"


# ---------------- 增量更新 ----------------


async def test_update_gacha_log_incremental(data_dir, authkey_info):
    """本地已有部分记录时只追加新记录；翻到整页已存在的页时提前停止"""
    page1 = make_records(range(100, 80, -1))  # 100..81 全新
    page2 = make_records(range(80, 77, -1))  # 80..78 本地已有
    store.write_gacha_log(1, UID, 301, "gs", make_records(range(80, 77, -1)))
    calls = []

    def handler(request: httpx.Request):
        params = dict(request.url.params)
        calls.append(params)
        return api_ok(page1 if params["page"] == "1" else page2)

    with respx.mock(assert_all_called=False) as router:
        router.get(GS_URL).mock(side_effect=handler)
        result = await gacha_log.update_gacha_log(1, UID, authkey_info, pool_types=[301])

    assert result == {301: 20}
    assert len(calls) == 2  # 第二页整页已存在，停止翻页
    saved = store.read_gacha_log(1, UID, 301, "gs")
    assert [r["id"] for r in saved] == [str(i) for i in range(100, 77, -1)]  # 合并去重按 id 降序


async def test_update_gacha_log_no_new(data_dir, authkey_info):
    """第一页就整页已存在：只发一次请求，新增 0 条"""
    local = make_records(range(100, 80, -1))
    store.write_gacha_log(1, UID, 301, "gs", local)
    calls = []

    def handler(request: httpx.Request):
        calls.append(dict(request.url.params))
        return api_ok(local)

    with respx.mock(assert_all_called=False) as router:
        router.get(GS_URL).mock(side_effect=handler)
        result = await gacha_log.update_gacha_log(1, UID, authkey_info, pool_types=[301])

    assert result == {301: 0}
    assert len(calls) == 1  # 提前停止，没有多余翻页
    assert store.read_gacha_log(1, UID, 301, "gs") == local


# ---------------- authkey 缓存 ----------------


def test_authkey_cache(data_dir):
    info = {"authkey": "k/=", "region": "cn_gf01", "game": "gs"}
    assert gacha_log.load_authkey(1, "gs") is None
    gacha_log.save_authkey(1, "gs", info)
    loaded = gacha_log.load_authkey(1, "gs")
    assert loaded == info
    # 分用户/分游戏隔离
    assert gacha_log.load_authkey(2, "gs") is None
    assert gacha_log.load_authkey(1, "sr") is None


def test_authkey_cache_expired(data_dir, monkeypatch):
    gacha_log.save_authkey(1, "gs", {"authkey": "k", "region": "cn_gf01", "game": "gs"})
    # 时间快进到 24h + 1s 后
    future = gacha_log.time.time() + gacha_log.AUTHKEY_TTL + 1
    monkeypatch.setattr(gacha_log, "time", types.SimpleNamespace(time=lambda: future))
    assert gacha_log.load_authkey(1, "gs") is None


# ---------------- UIGF 导入导出 ----------------


def _seed_local_logs(user_id):
    """写入两个卡池的本地记录：301 两条（其中一条 gacha_type=400）、302 一条"""
    logs_301 = make_records([100, 99])
    logs_301[1]["gacha_type"] = "400"
    store.write_gacha_log(user_id, UID, 301, "gs", logs_301)
    store.write_gacha_log(user_id, UID, 302, "gs", make_records([98], gacha_type=302))


def test_export_uigf(data_dir):
    _seed_local_logs(1)
    data = uigf.export_uigf(1, UID, "gs")
    info = data["info"]
    assert info["uid"] == UID
    assert info["uigf_version"] == "v2.3"
    assert info["export_app"] == "nonebot-plugin-miao"
    assert info["lang"] == "zh-cn"
    assert len(data["list"]) == 3
    # 按 id 升序，且 400 归入 301
    assert [r["id"] for r in data["list"]] == ["98", "99", "100"]
    assert {r["uigf_gacha_type"] for r in data["list"] if r["gacha_type"] == "400"} == {"301"}


def test_export_uigf_sr(data_dir):
    store.write_gacha_log(1, "800000001", 11, "sr", make_records([5, 4], gacha_type=11))
    data = uigf.export_uigf(1, "800000001", "sr")
    assert data["info"]["srgf_version"] == "v1.0"
    assert "uigf_version" not in data["info"]
    assert len(data["list"]) == 2


def test_import_uigf(data_dir):
    _seed_local_logs(1)
    exported = uigf.export_uigf(1, UID, "gs")
    # 导入到另一个用户的空存储，条数不变（往返一致）
    result = uigf.import_uigf(2, exported)
    assert result == {301: 2, 302: 1}
    assert len(store.read_gacha_log(2, UID, 301, "gs")) == 2
    assert len(store.read_gacha_log(2, UID, 302, "gs")) == 1
    # 再次导入全部去重，新增 0
    assert uigf.import_uigf(2, exported) == {301: 0, 302: 0}


def test_import_uigf_invalid(data_dir):
    with pytest.raises(GachaLogError, match="非统一祈愿记录标准"):
        uigf.import_uigf(1, {"foo": "bar"})
    with pytest.raises(GachaLogError, match="缺少必要字段"):
        uigf.import_uigf(1, {"info": {"uid": UID, "uigf_version": "v2.3"}, "list": [{"id": "1"}]})
    with pytest.raises(GachaLogError, match="记录列表为空"):
        uigf.import_uigf(1, {"info": {"uid": UID, "uigf_version": "v2.3"}, "list": []})
