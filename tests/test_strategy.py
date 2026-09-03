"""原神攻略图数据源测试：合集解析、来源 4 特殊图片、缓存与异常回退。"""
from __future__ import annotations

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


def test_source_validation():
    assert strategy.source_name(1) == "西风驿站"
    with pytest.raises(ValueError, match="1-7"):
        strategy.source_name(8)


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "strategy-cache"
    monkeypatch.setattr(strategy, "_cache_root", lambda: root)
    return root


async def test_fetch_strategy_image_writes_and_reuses_cache(cache_root: Path, monkeypatch: pytest.MonkeyPatch):
    calls = 0

    async def fake_remote(role_name: str, source: int, client: httpx.AsyncClient) -> bytes:
        nonlocal calls
        calls += 1
        assert role_name == "芙宁娜"
        assert source == 2
        return b"\xff\xd8\xffstrategy-image"

    monkeypatch.setattr(strategy, "_fetch_remote", fake_remote)
    async with httpx.AsyncClient() as client:
        first = await strategy.fetch_strategy_image("芙宁娜", 2, client=client)
        second = await strategy.fetch_strategy_image("芙宁娜", 2, client=client)

    assert first == second == b"\xff\xd8\xffstrategy-image"
    assert calls == 1
    assert strategy.cache_path("芙宁娜", 2).read_bytes() == first


async def test_fetch_strategy_image_refreshes_cache(cache_root: Path, monkeypatch: pytest.MonkeyPatch):
    path = strategy.cache_path("芙宁娜", 1)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")

    async def fake_remote(role_name: str, source: int, client: httpx.AsyncClient) -> bytes:
        return b"new"

    monkeypatch.setattr(strategy, "_fetch_remote", fake_remote)
    async with httpx.AsyncClient() as client:
        data = await strategy.fetch_strategy_image("芙宁娜", 1, refresh=True, client=client)

    assert data == b"new"
    assert path.read_bytes() == b"new"


async def test_fetch_payloads_all_failed(monkeypatch: pytest.MonkeyPatch):
    async def fail(client: httpx.AsyncClient, collection_id: int) -> dict:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(strategy, "_fetch_collection", fail)
    async with httpx.AsyncClient() as client:
        with pytest.raises(strategy.StrategyDataError, match="暂时不可用"):
            await strategy._fetch_payloads(client, 2)
