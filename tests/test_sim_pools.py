"""官方卡池录制数据解析、缓存切换、失败保留与按需自动更新。"""
import asyncio
from copy import deepcopy

import httpx
import pytest
import respx

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.datasource import sim_pools

NOW = sim_pools.timestamp("2026-09-10 12:00:00")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sim_pools.time, "time", lambda: NOW)
    monkeypatch.setattr(sim_pools, "_RETRY_AFTER", {})
    monkeypatch.setattr(sim_pools, "_LOCKS", {game: asyncio.Lock() for game in sim_pools.GAME_NAMES})


def routes(mock, game, recorded):
    base = sim_pools.BASE_URLS[game]
    listing = mock.get(f"{base}/gacha/list.json").mock(return_value=httpx.Response(200, json=recorded["list"]))
    for gid, detail in recorded["details"].items():
        mock.get(f"{base}/{gid}/zh-cn.json").mock(return_value=httpx.Response(200, json=detail))
    return listing


@pytest.mark.parametrize("game,count", [("gs", 4), ("sr", 7), ("zzz", 5)])
async def test_official_full_rosters_and_multiple_pools(official_pools, game, count):
    with respx.mock() as mock:
        routes(mock, game, official_pools[game])
        snapshot = await sim_pools.ensure_pools(game)
    pools = sim_pools.active_pools(snapshot)
    assert len(pools) == count
    assert len([p for p in pools if p["kind"] == "role"]) >= 2
    assert {p["kind"] for p in pools} == {"role", "weapon", "permanent"}
    second = sim_pools.select_pool(pools, "role2")
    first = sim_pools.select_pool(pools, "role")
    assert second["up5"] != first["up5"]
    for pool in pools:
        assert pool["weapon3"]
        assert all(pool["items"][name]["type"] == "weapon" for name in pool["weapon3"])
        assert pool["items"][pool["up5"][0]]["imgFile"].startswith("https://") if pool["up5"] else True
    weapon = sim_pools.select_pool(pools, "weapon")
    assert weapon["upRate5"] == 7500
    assert weapon["rate5"] == {"gs": 70, "sr": 80, "zzz": 100}[game]
    assert sim_pools.read_snapshot(game) == snapshot


async def test_fetch_once_within_ttl_and_concurrent_refresh(official_pools):
    with respx.mock() as mock:
        listing = routes(mock, "gs", official_pools["gs"])
        results = await asyncio.gather(*(sim_pools.ensure_pools("gs") for _ in range(4)))
        assert listing.call_count == 1
        assert all(r == results[0] for r in results)
        await sim_pools.ensure_pools("gs", force=True)
        assert listing.call_count == 2


async def test_failed_partial_update_keeps_old_cache(official_pools):
    recorded = official_pools["gs"]
    with respx.mock() as mock:
        routes(mock, "gs", recorded)
        snapshot = await sim_pools.ensure_pools("gs")
        original = sim_pools.cache_path("gs").read_bytes()
        gid = recorded["list"]["data"]["list"][2]["gacha_id"]
        mock.get(f"{sim_pools.BASE_URLS['gs']}/{gid}/zh-cn.json").mock(return_value=httpx.Response(503))
        with pytest.raises(sim_pools.PoolError, match="同步失败"):
            await sim_pools.ensure_pools("gs", force=True)
        assert sim_pools.cache_path("gs").read_bytes() == original
        snapshot["fetchedAt"] -= 7200
        store.save_json(sim_pools.cache_path("gs"), snapshot)
        fallback = await sim_pools.ensure_pools("gs")
        assert fallback["warning"]
        assert sim_pools.select_pool(sim_pools.active_pools(fallback), "role2")


async def test_unavailable_without_cache_retries_later():
    with respx.mock() as mock:
        listing = mock.get(f"{sim_pools.BASE_URLS['zzz']}/gacha/list.json").mock(return_value=httpx.Response(500))
        for _ in range(2):
            with pytest.raises(sim_pools.PoolError):
                await sim_pools.ensure_pools("zzz")
        assert listing.call_count == 1
        assert not sim_pools.cache_path("zzz").exists()


async def test_expired_pool_never_served_on_network_failure(official_pools):
    with respx.mock() as mock:
        routes(mock, "gs", official_pools["gs"])
        snapshot = await sim_pools.ensure_pools("gs")
        for pool in snapshot["pools"]:
            pool["end"] = NOW - 1
        snapshot["fetchedAt"] = NOW - 7200
        store.save_json(sim_pools.cache_path("gs"), snapshot)
        mock.get(f"{sim_pools.BASE_URLS['gs']}/gacha/list.json").mock(return_value=httpx.Response(500))
        with pytest.raises(sim_pools.PoolError):
            await sim_pools.ensure_pools("gs")
        assert sim_pools.active_pools(sim_pools.read_snapshot("gs")) == []


async def test_boundary_forces_refresh_before_ttl(official_pools, monkeypatch):
    with respx.mock() as mock:
        listing = routes(mock, "gs", official_pools["gs"])
        snapshot = await sim_pools.ensure_pools("gs")
        snapshot["pools"][0]["end"] = NOW + 10
        store.save_json(sim_pools.cache_path("gs"), snapshot)
        monkeypatch.setattr(sim_pools.time, "time", lambda: NOW + 11)
        await sim_pools.ensure_pools("gs")
        assert listing.call_count == 2


def test_time_window_both_bounds_and_number_validation():
    pools = [{"start": NOW - 10, "end": NOW, "kind": "role"},
             {"start": NOW + 1, "end": NOW + 20, "kind": "role"}]
    assert sim_pools.active_pools({"pools": pools}, NOW) == pools[:1]
    assert sim_pools.active_pools({"pools": pools}, NOW + 1) == pools[1:]
    for kind in ("role2", "role0", "weapon", "garbage"):
        with pytest.raises(sim_pools.PoolError):
            sim_pools.select_pool(pools[:1], kind)
    assert sim_pools.timestamp("1970-01-01 08:00:00") == 0


@pytest.mark.parametrize("damage", ["empty", "wrong_type", "rate", "future", "non_up"])
def test_malformed_data_rejected(official_pools, damage):
    recorded = official_pools["gs"]
    row = deepcopy(recorded["list"]["data"]["list"][1])
    detail = deepcopy(recorded["details"][row["gacha_id"]])
    if damage == "empty":
        detail["r3_prob_list"] = []
    elif damage == "wrong_type":
        detail["gacha_type"] = 302
    elif damage == "rate":
        detail["r5_prob"] = "999%"
    elif damage == "future":
        row["begin_time"] = row["end_time"]
    else:
        detail["r5_prob_list"] = [v for v in detail["r5_prob_list"] if v["is_up"]]
    with pytest.raises(sim_pools.PoolError):
        sim_pools.parse_pool("gs", row, detail)


async def test_new_period_roster_replaces_cache(official_pools):
    with respx.mock() as mock:
        routes(mock, "zzz", official_pools["zzz"])
        first = await sim_pools.ensure_pools("zzz")
        new = deepcopy(official_pools["zzz"])
        row = new["list"]["data"]["list"][0]
        detail = new["details"].pop(row["gacha_id"])
        row["gacha_id"] = "next-version"
        for field in ("items_avatar_star_5", "items_up_star_5"):
            detail[field][0]["item_name"] = "新版本角色"
        new["details"][row["gacha_id"]] = detail
        routes(mock, "zzz", new)
        updated = await sim_pools.ensure_pools("zzz", force=True)
        assert sim_pools.select_pool(sim_pools.active_pools(updated), "role")["up5"] == ["新版本角色"]
        assert sim_pools.select_pool(sim_pools.active_pools(first), "role")["id"] != "next-version"
