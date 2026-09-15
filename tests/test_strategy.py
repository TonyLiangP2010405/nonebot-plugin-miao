"""三游戏攻略图数据源测试：合集解析、来源、缓存与接口回退。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from nonebot_plugin_miao.datasource import strategy


def _payload(
    role_name: str,
    images: list[dict] | None = None,
    *,
    subject: str | None = None,
    content: str = "",
    post_id: int = 100,
) -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "posts": [
                {
                    "post": {
                        "post_id": str(post_id),
                        "subject": subject if subject is not None else f"{role_name}角色攻略",
                        "structured_content": content,
                    },
                    "image_list": images
                    or [
                        {"image_id": "small", "url": "https://img.example/small.jpg", "size": 10},
                        {"image_id": "large", "url": "https://img.example/large.jpg", "size": 100},
                    ],
                }
            ]
        },
    }


def test_find_strategy_image_uses_largest_image():
    url = strategy.find_strategy_image_url("芙宁娜", 1, [_payload("芙宁娜")])
    assert url == "https://img.example/large.jpg"


def test_find_strategy_image_source_four_uses_image_id():
    images = [
        {"image_id": "wrong", "url": "https://img.example/wrong.jpg", "size": 1000},
        {"image_id": "target", "url": "https://img.example/target.jpg", "size": 10},
    ]
    content = json.dumps([{"insert": "【芙宁娜】"}, {"insert": {"image": "target"}}], ensure_ascii=False)
    url = strategy.find_strategy_image_url("芙宁娜", 4, [_payload("芙宁娜", images, subject="合集", content=content)])
    assert url == "https://img.example/target.jpg"


def test_source_four_uses_each_role_heading_instead_of_preface_mentions():
    images = [{"image_id": role, "url": f"https://img.example/{role}.jpg"} for role in ("安柏", "凯亚", "丽莎")]
    operations = [{"insert": "这次带来安柏、凯亚、丽莎的一图流攻略。\n"}]
    for role in ("安柏", "凯亚", "丽莎"):
        operations.extend([{"insert": f"【{role}】"}, {"insert": {"image": role}}])
    payload = _payload("安柏", images, subject="御三家·一图看懂", content=json.dumps(operations, ensure_ascii=False))
    for role in ("安柏", "凯亚", "丽莎"):
        assert strategy.find_strategy_image_url(role, 4, [payload]) == f"https://img.example/{role}.jpg"


def test_source_four_fold_title_binds_its_own_image():
    images = [
        {"image_id": "xiao", "url": "https://img.example/xiao.jpg"},
        {"image_id": "ganyu", "url": "https://img.example/ganyu.jpg"},
    ]
    operations = [
        {"insert": "各位旅行者们，带来魈和甘雨攻略。"},
        {
            "insert": {
                "fold": {
                    "title": json.dumps([{"insert": "风丨【护法夜叉——魈】"}], ensure_ascii=False),
                    "content": json.dumps([{"insert": {"image": "xiao"}}], ensure_ascii=False),
                }
            }
        },
        {
            "insert": {
                "fold": {
                    "title": json.dumps([{"insert": "冰丨【循循守月——甘雨】"}], ensure_ascii=False),
                    "content": json.dumps([{"insert": {"image": "ganyu"}}], ensure_ascii=False),
                }
            }
        },
    ]
    payload = _payload("魈", images, subject="魈✿甘雨一图流", content=json.dumps(operations, ensure_ascii=False))
    assert strategy.find_strategy_image_url("魈", 4, [payload]) == "https://img.example/xiao.jpg"
    assert strategy.find_strategy_image_url("甘雨", 4, [payload]) == "https://img.example/ganyu.jpg"
    assert strategy.find_strategy_image_url("旅行者", 4, [payload]) is None


def test_find_strategy_image_missing():
    assert strategy.find_strategy_image_url("芙宁娜", 1, [_payload("胡桃")]) is None


def test_find_strategy_image_normalizes_punctuation_and_pro():
    content = json.dumps(
        [{"insert": "【攻略】姬子·启行一图流", "attributes": {"link": "https://www.miyoushe.com/sr/article/123"}}],
        ensure_ascii=False,
    )
    himeko = _payload("姬子", subject="【车站指南】姬子·启行 攻略合集", content=content)
    assert strategy.find_strategy_article_ids("姬子•启行", [himeko]) == [123]

    content = json.dumps(
        [{"insert": "流萤加强后培养攻略", "attributes": {"link": "https://www.miyoushe.com/sr/article/456"}}],
        ensure_ascii=False,
    )
    firefly = _payload("流萤", subject="【V3.4攻略】流萤加强后培养攻略", content=content)
    assert strategy.find_strategy_article_ids("流萤Pro", [firefly]) == [456, 100]


def test_find_strategy_article_uses_matching_link_in_multi_role_post():
    content = json.dumps(
        [
            {
                "insert": "大丽花养成攻略",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/111"},
            },
            {
                "insert": "流萤全新配速与养成一图流",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/222"},
            },
        ],
        ensure_ascii=False,
    )
    payload = _payload("流萤", subject="大丽花&流萤攻略合集", content=content, post_id=333)
    assert strategy.find_strategy_article_ids("流萤", [payload]) == [222]


def test_star_rail_guide_links_prioritize_role_one_page_guide():
    content = json.dumps(
        [
            {
                "insert": "老角色加强详细对比丨花火＆黑天鹅",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/73083037"},
            },
            {
                "insert": "「黑天鹅」机制加强解析攻略",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/73129317"},
            },
            {
                "insert": "黑天鹅培养一图流丨配队丨参考面板",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/57434916"},
            },
        ],
        ensure_ascii=False,
    )
    payload = _payload("黑天鹅", subject="黑天鹅攻略合集", content=content, post_id=73259957)
    assert strategy.find_strategy_article_ids("黑天鹅", [payload], "sr") == [
        57434916,
        73129317,
        73083037,
    ]


def test_star_rail_one_page_guides_prefer_newer_version():
    content = json.dumps(
        [
            {
                "insert": "【V2.5攻略】黑天鹅丨培养一图流",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/57434916"},
            },
            {
                "insert": "【V2.0攻略】黑天鹅全方位养成攻略丨一图看懂",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/48855731"},
            },
        ],
        ensure_ascii=False,
    )
    payload = _payload("黑天鹅", subject="黑天鹅攻略合集", content=content)
    assert strategy.find_strategy_article_ids("黑天鹅", [payload], "sr") == [57434916, 48855731]


def test_star_rail_guide_links_ignore_other_game():
    content = json.dumps(
        [
            {
                "insert": "昔涟全方位一图流",
                "attributes": {"link": "https://www.miyoushe.com/zzz/article/111"},
            },
            {
                "insert": "昔涟全方位一图流",
                "attributes": {"link": "https://www.miyoushe.com/sr/article/222"},
            },
        ],
        ensure_ascii=False,
    )
    payload = _payload("昔涟", subject="昔涟攻略合集", content=content)
    assert strategy.find_strategy_article_ids("昔涟", [payload], "sr") == [222]


def test_role_match_does_not_confuse_new_forms_with_old_roles():
    assert not strategy._role_in_text("黑塔", "大黑塔培养一图流")
    assert not strategy._role_in_text("刃", "千冶·刃角色攻略")
    assert not strategy._role_in_text("砂金", "砂金·戏浪角色攻略")
    assert not strategy._role_in_text("银狼", "银狼LV.999角色攻略")
    assert strategy._role_in_text("大黑塔", "大黑塔培养一图流")
    assert strategy._role_in_text("刃Pro", "刃角色攻略")
    assert strategy._role_in_text("刃Pro", "千冶·刃角色攻略")
    assert strategy._role_in_text("昔涟", "昔涟·攻略合集")
    assert strategy._role_in_text("刃", "刃·攻略合集")


def test_multi_role_overview_is_not_used_as_a_single_role_guide():
    overview = _payload(
        "纳西妲",
        subject="【卡池全攻略】纳西妲、妮露、久岐忍、莱依拉、多莉一图看懂",
    )
    own_guide = _payload("纳西妲", subject="纳西妲入门攻略 一图总结+详解")
    assert strategy.find_strategy_image_url("妮露", 6, [overview], "gs") is None
    assert strategy.find_strategy_image_url("纳西妲", 6, [overview, own_guide], "gs") == (
        "https://img.example/large.jpg"
    )


def test_slash_separated_character_overview_is_distinct_from_guide_sections():
    assert strategy._is_collection_overview("千夏/仪玄/可琳/比利一图流")
    assert not strategy._is_collection_overview("夜兰武器/圣遗物/配队一图流")
    assert not strategy._is_collection_overview("奥菲丝&鬼火角色攻略")
    assert strategy._is_collection_overview("Saber丨Archer养成一图流")
    assert strategy._is_collection_overview("Saber&Archer养成一图流")
    overview = _payload("千夏", subject="千夏/仪玄/可琳/比利一图流", post_id=111)
    guide = _payload("千夏", subject="千夏养成角色攻略", post_id=222)
    assert strategy.find_strategy_article_ids("千夏", [overview, guide], "zzz") == [222]


def test_genshin_body_guide_beats_larger_landscape_decoration():
    images = [
        {"url": "https://img.example/cover.jpg", "width": 7623, "height": 1499, "size": 607129},
        {"url": "https://img.example/guide.jpg", "width": 2130, "height": 3784, "size": 4779747},
    ]
    assert strategy.find_strategy_image_url("纳西妲", 6, [_payload("纳西妲", images)], "gs") == (
        "https://img.example/guide.jpg"
    )


def test_role_guide_is_preferred_to_earlier_miscellaneous_post():
    warning = _payload("迪希雅", subject="【练迪希雅必看】千万不要陷入迪希雅的三大常见误区")
    guide = _payload("迪希雅", subject="【V3.5攻略·角色攻略】迪希雅入门攻略 一图总结+详解")
    assert strategy.find_strategy_image_url("迪希雅", 6, [warning, guide], "gs") == ("https://img.example/large.jpg")


def test_find_post_guide_image_excludes_cover_and_uses_largest_body_image():
    payload = {
        "data": {
            "post": {
                "cover": {"image_id": "cover", "url": "https://img.example/cover.jpg"},
                "image_list": [
                    {"image_id": "cover", "url": "https://img.example/cover.jpg", "size": 9999},
                    {"image_id": "small", "url": "https://img.example/small.jpg", "size": 100},
                    {"image_id": "guide", "url": "https://img.example/guide.jpg", "size": 5000},
                ],
            }
        }
    }
    assert strategy.find_post_guide_image_url(payload) == "https://img.example/guide.jpg"


def test_find_post_guide_image_does_not_return_cover_only_post():
    payload = {
        "data": {
            "post": {
                "cover": {"image_id": "cover", "url": "https://img.example/cover.jpg"},
                "image_list": [{"image_id": "cover", "url": "https://img.example/cover.jpg", "size": 9999}],
            }
        }
    }
    assert strategy.find_post_guide_image_url(payload) is None


def test_find_post_guide_image_prefers_real_long_guide_over_large_file():
    payload = {
        "data": {
            "post": {
                "cover": {"image_id": "cover", "url": "https://img.example/cover.jpg"},
                "image_list": [
                    {
                        "image_id": "cover",
                        "url": "https://img.example/cover.jpg",
                        "width": 1772,
                        "height": 997,
                        "size": 2029804,
                    },
                    {
                        "image_id": "guide",
                        "url": "https://img.example/guide.jpg",
                        "width": 1772,
                        "height": 8800,
                        "size": 12914798,
                    },
                    {
                        "image_id": "misleading",
                        "url": "https://img.example/misleading.jpg",
                        "width": 671,
                        "height": 361,
                        "size": 14174684,
                    },
                ],
            }
        }
    }
    assert strategy.find_post_guide_image_url(payload) == "https://img.example/guide.jpg"


def test_source_validation():
    assert strategy.source_name(1) == "西风驿站"
    assert strategy.source_name(1, "sr") == "列车广播（车站指南）"
    assert strategy.source_name(1, "zzz") == "绳么东西BROADCAST（角色攻略合集）"
    assert strategy.source_count("gs") == 7
    assert strategy.source_count("sr") == 3
    assert strategy.source_count("zzz") == 4
    with pytest.raises(ValueError, match="1-7"):
        strategy.source_name(8)
    with pytest.raises(ValueError, match="1-3"):
        strategy.source_name(4, "sr")
    with pytest.raises(ValueError, match="非法攻略游戏标识"):
        strategy.source_name(1, "xx")


def test_collection_ids_are_separated_by_game():
    assert strategy.COLLECTION_IDS_BY_GAME["sr"][1] == (917513,)
    assert strategy.COLLECTION_IDS_BY_GAME["zzz"][1] == (3156883,)
    assert strategy.GAME_GIDS == {"gs": 2, "sr": 6, "zzz": 8}


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "strategy-cache"
    monkeypatch.setattr(strategy, "_cache_root", lambda: root)
    return root


async def test_fetch_strategy_image_writes_and_reuses_cache(cache_root: Path, monkeypatch: pytest.MonkeyPatch):
    calls = 0

    async def fake_remote(role_name: str, source: int, game: str, client: httpx.AsyncClient) -> bytes:
        nonlocal calls
        calls += 1
        assert role_name == "芙宁娜"
        assert source == 2
        assert game == "gs"
        return b"\xff\xd8\xffstrategy-image"

    monkeypatch.setattr(strategy, "_fetch_remote", fake_remote)
    async with httpx.AsyncClient() as client:
        first = await strategy.fetch_strategy_image("芙宁娜", 2, client=client)
        second = await strategy.fetch_strategy_image("芙宁娜", 2, client=client)

    assert first == second == b"\xff\xd8\xffstrategy-image"
    assert calls == 1
    assert strategy.cache_path("芙宁娜", 2).read_bytes() == first


def test_cache_paths_are_isolated_by_game(cache_root: Path):
    assert strategy.cache_path("流萤", 1, "sr") == cache_root / "guide-v4" / "sr" / "1" / "流萤.jpg"
    assert strategy.cache_path("星见雅", 1, "zzz") == cache_root / "guide-v4" / "zzz" / "1" / "星见雅.jpg"
    assert strategy.cache_path("心海", 1, "gs") == cache_root / "guide-v4" / "gs" / "1" / "心海.jpg"


async def test_fetch_strategy_image_refreshes_cache(cache_root: Path, monkeypatch: pytest.MonkeyPatch):
    path = strategy.cache_path("芙宁娜", 1)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")

    async def fake_remote(role_name: str, source: int, game: str, client: httpx.AsyncClient) -> bytes:
        return b"new"

    monkeypatch.setattr(strategy, "_fetch_remote", fake_remote)
    async with httpx.AsyncClient() as client:
        data = await strategy.fetch_strategy_image("芙宁娜", 1, refresh=True, client=client)

    assert data == b"new"
    assert path.read_bytes() == b"new"


async def test_fetch_payloads_all_failed(monkeypatch: pytest.MonkeyPatch):
    async def fail(client: httpx.AsyncClient, collection_id: int, game: str) -> dict:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(strategy, "_fetch_collection", fail)
    async with httpx.AsyncClient() as client:
        with pytest.raises(strategy.StrategyDataError, match="暂时不可用"):
            await strategy._fetch_payloads(client, 2, "gs")


async def test_fetch_remote_follows_article_and_downloads_body_guide(monkeypatch: pytest.MonkeyPatch):
    content = json.dumps(
        [
            {
                "insert": "星见雅角色攻略一图流",
                "attributes": {"link": "https://www.miyoushe.com/zzz/article/60271370"},
            }
        ],
        ensure_ascii=False,
    )
    collection = _payload("星见雅", subject="星见雅角色攻略合集", content=content, post_id=60376649)
    article = {
        "retcode": 0,
        "data": {
            "post": {
                "post": {"subject": "【2.5攻略征集】星见雅角色攻略一图流"},
                "cover": {"image_id": "cover", "url": "https://img.example/cover.jpg"},
                "image_list": [
                    {"image_id": "guide", "url": "https://img.example/guide.jpg", "size": 6000},
                    {"image_id": "cover", "url": "https://img.example/cover.jpg", "size": 9000},
                ],
            }
        },
    }

    async def fake_payloads(client: httpx.AsyncClient, source: int, game: str) -> list[dict]:
        assert (source, game) == (1, "zzz")
        return [collection]

    async def fake_post(client: httpx.AsyncClient, post_id: int, game: str) -> dict:
        assert (post_id, game) == (60271370, "zzz")
        return article

    async def fake_download(client: httpx.AsyncClient, url: str) -> bytes:
        assert url == "https://img.example/guide.jpg"
        return b"real-guide"

    monkeypatch.setattr(strategy, "_fetch_payloads", fake_payloads)
    monkeypatch.setattr(strategy, "_fetch_post", fake_post)
    monkeypatch.setattr(strategy, "_download_image", fake_download)
    async with httpx.AsyncClient() as client:
        assert await strategy._fetch_remote("星见雅", 1, "zzz", client) == b"real-guide"


async def test_fetch_remote_checks_article_title_before_using_its_images(monkeypatch: pytest.MonkeyPatch):
    content = json.dumps(
        [
            {"insert": "黑塔培养一图流", "attributes": {"link": "https://www.miyoushe.com/sr/article/111"}},
            {"insert": "黑塔角色攻略", "attributes": {"link": "https://www.miyoushe.com/sr/article/222"}},
        ],
        ensure_ascii=False,
    )
    collection = _payload("黑塔", subject="黑塔攻略合集", content=content)

    async def fake_payloads(client: httpx.AsyncClient, source: int, game: str) -> list[dict]:
        return [collection]

    async def fake_post(client: httpx.AsyncClient, post_id: int, game: str) -> dict:
        title = "大黑塔培养一图流" if post_id == 111 else "黑塔角色攻略"
        return {
            "data": {
                "post": {
                    "post": {"subject": title},
                    "image_list": [{"url": f"https://img.example/{post_id}.jpg", "size": 100}],
                }
            }
        }

    async def fake_download(client: httpx.AsyncClient, url: str) -> bytes:
        assert url == "https://img.example/222.jpg"
        return b"black-tower-guide"

    monkeypatch.setattr(strategy, "_fetch_payloads", fake_payloads)
    monkeypatch.setattr(strategy, "_fetch_post", fake_post)
    monkeypatch.setattr(strategy, "_download_image", fake_download)
    async with httpx.AsyncClient() as client:
        assert await strategy._fetch_remote("黑塔", 1, "sr", client) == b"black-tower-guide"


async def test_fetch_collection_uses_game_gid_and_falls_back_to_old_host():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "bbs-api.miyoushe.com":
            return httpx.Response(503)
        return httpx.Response(200, json=_payload("流萤"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await strategy._fetch_collection(client, 917513, "sr")

    assert payload["retcode"] == 0
    assert [request.url.host for request in requests] == ["bbs-api.miyoushe.com", "bbs-api.mihoyo.com"]
    assert all(request.url.params["gids"] == "6" for request in requests)


async def test_fetch_post_uses_game_gid_and_falls_back_to_old_host():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "bbs-api.miyoushe.com":
            return httpx.Response(503)
        return httpx.Response(200, json={"retcode": 0, "message": "OK", "data": {"post": {}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await strategy._fetch_post(client, 60271370, "zzz")

    assert payload["retcode"] == 0
    assert [request.url.host for request in requests] == ["bbs-api.miyoushe.com", "bbs-api.mihoyo.com"]
    assert all(request.url.params["gids"] == "8" for request in requests)
    assert all(request.url.params["post_id"] == "60271370" for request in requests)
