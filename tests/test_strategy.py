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
) -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "posts": [
                {
                    "post": {
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
    content = r'[{"insert":"【芙宁娜】"},{"insert":{"image":"target"}}]'
    url = strategy.find_strategy_image_url("芙宁娜", 4, [_payload("芙宁娜", images, subject="合集", content=content)])
    assert url == "https://img.example/target.jpg"


def test_find_strategy_image_missing():
    assert strategy.find_strategy_image_url("芙宁娜", 1, [_payload("胡桃")]) is None


def test_find_strategy_image_normalizes_punctuation_and_pro():
    himeko = _payload("姬子", subject="【车站指南】姬子·启行 攻略合集")
    assert strategy.find_strategy_image_url("姬子•启行", 1, [himeko], "sr")
    firefly = _payload("流萤", subject="【V3.4攻略】流萤加强后培养攻略")
    assert strategy.find_strategy_image_url("流萤Pro", 2, [firefly], "sr")


def test_find_strategy_image_uses_matching_section_in_multi_role_post():
    images = [
        {"image_id": "cover", "url": "https://img.example/cover.jpg", "size": 9999, "width": 1200, "height": 675},
        {"image_id": "banner", "url": "https://img.example/banner.jpg", "size": 10, "width": 604, "height": 84},
        {"image_id": "other", "url": "https://img.example/dahlia.jpg", "size": 5000, "width": 1200, "height": 675},
        {"image_id": "target", "url": "https://img.example/firefly.jpg", "size": 100, "width": 568, "height": 756},
    ]
    content = json.dumps(
        [
            {"insert": {"image": "cover"}},
            {"insert": "本期介绍新角色大丽花和复刻角色流萤"},
            {"insert": {"image": "banner"}},
            {"insert": "新角色——大丽花\n大丽花养成攻略"},
            {"insert": {"image": "other"}},
            {"insert": "大丽花给流萤该如何使用"},
            {"insert": {"image": "other"}},
            {"insert": "复刻角色——流萤\n流萤全新配速与养成攻略"},
            {"insert": {"image": "target"}},
        ],
        ensure_ascii=False,
    )
    payload = _payload("流萤", images, subject="大丽花&流萤攻略合集", content=content)
    assert strategy.find_strategy_image_url("流萤", 1, [payload], "sr") == "https://img.example/firefly.jpg"


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
    assert strategy.cache_path("流萤", 1, "sr") == cache_root / "sr" / "1" / "流萤.jpg"
    assert strategy.cache_path("星见雅", 1, "zzz") == cache_root / "zzz" / "1" / "星见雅.jpg"
    assert strategy.cache_path("心海", 1, "gs") == cache_root / "1" / "心海.jpg"


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
