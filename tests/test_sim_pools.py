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
    if game in sim_pools.ICON_META_URLS:
        avatars = {}
        weapons = {}
        names = {}
        synthetic_id = 900000
        for detail in recorded["details"].values():
            for field, target in (
                ("items_avatar_star_5", avatars),
                ("items_avatar_star_4", avatars),
                ("items_light_cone_star_5", weapons),
                ("items_light_cone_star_4", weapons),
                ("items_light_cone_star_3", weapons),
            ):
                for item in detail.get(field) or []:
                    synthetic_id += 1
                    item_id = str(item.get("origin_item_id") or synthetic_id)
                    if game == "sr":
                        name_hash = str(synthetic_id)
                        names[name_hash] = item["item_name"]
                        target[item_id] = {
                            "AvatarCutinFrontImgPath" if target is avatars else "ImagePath": (
                                f"/ui/hsr/Item_{item_id}.png"
                            ),
                            "AvatarName" if target is avatars else "EquipmentName": {"Hash": name_hash},
                        }
                    else:
                        field = "Image" if target is avatars else "ImagePath"
                        target[item_id] = {field: f"/ui/zzz/Item_{item_id}.png"}
        mock.get(sim_pools.ICON_META_URLS[game][0]).mock(return_value=httpx.Response(200, json=avatars))
        mock.get(sim_pools.ICON_META_URLS[game][1]).mock(return_value=httpx.Response(200, json=weapons))
        if game == "sr":
            mock.get(sim_pools.ICON_META_URLS[game][2]).mock(return_value=httpx.Response(200, json={"zh-cn": names}))
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
        if game in sim_pools.ICON_META_URLS:
            assert all(item["imgFile"].startswith("https://") for item in pool["items"].values())
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
    pools = [{"start": NOW - 10, "end": NOW, "kind": "role"}, {"start": NOW + 1, "end": NOW + 20, "kind": "role"}]
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


def test_external_icon_metadata_is_validated_and_applied_without_overwrite():
    icons = sim_pools.parse_zzz_icon_meta(
        {
            "1011": {"Image": "/ui/zzz/IconRole01.png"},
            "1012": {"Image": "https://unsafe.example/role.png"},
            "bad": {"Image": "/ui/zzz/bad.png"},
        },
        {"12001": {"ImagePath": "/ui/zzz/Weapon_B_Common_01.png"}},
    )
    assert icons == {
        1011: "https://enka.network/ui/zzz/IconRole01.png",
        12001: "https://enka.network/ui/zzz/Weapon_B_Common_01.png",
    }
    pools = [
        {
            "game": "zzz",
            "items": {
                "代理人": {"itemId": 1011, "imgFile": ""},
                "官方图片": {"itemId": 12001, "imgFile": "https://official.example/up.png"},
            },
        }
    ]
    assert sim_pools.apply_item_icons("zzz", pools, icons) == 1
    assert pools[0]["items"]["代理人"]["imgFile"] == icons[1011]
    assert pools[0]["items"]["官方图片"]["imgFile"] == "https://official.example/up.png"
    assert sim_pools.parse_sr_icon_meta(
        {"1001": {"AvatarCutinFrontImgPath": "/ui/hsr/SpriteOutput/AvatarDrawCard/1001.png"}},
        {"20000": {"ImagePath": "/ui/hsr/SpriteOutput/LightConeFigures/20000.png"}},
        {"zh-cn": {}},
    )[0] == {
        1001: "https://enka.network/ui/hsr/SpriteOutput/AvatarDrawCard/1001.png",
        20000: "https://enka.network/ui/hsr/SpriteOutput/LightConeFigures/20000.png",
    }


@pytest.mark.parametrize("game", ["sr", "zzz"])
async def test_icon_sync_failure_reuses_cached_metadata(official_pools, game):
    recorded = official_pools[game]
    with respx.mock() as mock:
        routes(mock, game, recorded)
        first = await sim_pools.ensure_pools(game)
        assert first["itemImages"]
        if game == "sr":
            assert first["itemImageNames"]

        for url in sim_pools.ICON_META_URLS[game]:
            mock.get(url).mock(return_value=httpx.Response(503))
        refreshed = await sim_pools.ensure_pools(game, force=True)

    assert refreshed["itemImages"] == first["itemImages"]
    assert refreshed.get("itemImageNames") == first.get("itemImageNames")
    assert all(item["imgFile"].startswith("https://") for pool in refreshed["pools"] for item in pool["items"].values())


async def test_legacy_snapshot_forces_refresh_but_remains_failure_fallback(official_pools):
    legacy = {
        "schema": 1,
        "fetchedAt": NOW,
        "pools": [
            sim_pools.parse_pool("gs", row, official_pools["gs"]["details"][row["gacha_id"]])
            for row in official_pools["gs"]["list"]["data"]["list"]
        ],
    }
    store.save_json(sim_pools.cache_path("gs"), legacy)
    with respx.mock() as mock:
        listing = mock.get(f"{sim_pools.BASE_URLS['gs']}/gacha/list.json").mock(return_value=httpx.Response(503))
        fallback = await sim_pools.ensure_pools("gs")
    assert listing.called
    assert fallback["schema"] == 1
    assert fallback["warning"]
