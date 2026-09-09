"""三游戏模拟抽卡的保底、并行卡池、迁移、额度及并发回归测试。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.datasource import sim_pools
from nonebot_plugin_miao.gacha import simulate

SCOPE = "12345:67890"


class FakeRng:
    def __init__(self, roll=10000):
        self.roll = roll

    def __call__(self, minimum, maximum):
        return self.roll if maximum == 10000 else minimum


def counter(game, group, **updates):
    user = simulate.load_user(SCOPE, game)
    user.setdefault(group, simulate._counter()).update(updates)
    simulate.save_user(SCOPE, user, game)


@pytest.mark.parametrize("game", ["gs", "sr", "zzz"])
@pytest.mark.parametrize("kind,hard", [("role", 90), ("weapon", 80), ("permanent", 90)])
def test_hard_pity_exact_boundary(sim_data, game, kind, hard):
    counter(game, kind, num5=hard - 1)
    result = simulate.do_gacha(SCOPE, kind, game=game, count=1, rng=FakeRng())
    assert result["list"][0]["star"] == 5
    assert result["list"][0]["num"] == hard
    assert simulate.load_user(SCOPE, game)[kind]["num5"] == 0


@pytest.mark.parametrize("game", ["gs", "sr", "zzz"])
def test_second_role_and_equipment_pools(sim_data, game):
    second = simulate.get_pool("role2", game=game)
    counter(game, "role", num5=89, isUp5=1)
    result = simulate.do_gacha(SCOPE, "role2", game=game, count=1, rng=FakeRng())
    assert result["list"][0]["name"] == second["up5"][0]
    assert result["list"][0]["isBigUP"]
    assert simulate.load_user(SCOPE, game)["role"]["num5"] == 0
    if game != "gs":
        second_weapon = simulate.get_pool("weapon2", game=game)
        counter(game, "weapon", num5=79, isUp5=1)
        result = simulate.do_gacha(SCOPE, "weapon2", game=game, count=1, rng=FakeRng())
        assert result["list"][0]["name"] == second_weapon["up5"][0]
        assert not result["bingWeapon"]


@pytest.mark.parametrize("game", ["gs", "sr", "zzz"])
@pytest.mark.parametrize("kind", ["role", "weapon"])
def test_big_pity_and_four_star_guarantee(sim_data, game, kind):
    counter(game, kind, num5=89 if kind == "role" else 79)
    first = simulate.do_gacha(SCOPE, kind, game=game, count=1, rng=FakeRng())
    assert first["list"][0]["name"] not in simulate.get_pool(kind, game=game)["up5"]
    assert simulate.load_user(SCOPE, game)[kind]["isUp5"] == 1
    counter(game, kind, num5=89 if kind == "role" else 79)
    second = simulate.do_gacha(SCOPE, kind, game=game, count=1, rng=FakeRng())
    assert second["list"][0]["isBigUP"]
    counter(game, kind, num4=9)
    result = simulate.do_gacha(SCOPE, kind, game=game, count=1, rng=FakeRng())
    assert result["list"][0]["star"] == 4
    assert simulate.load_user(SCOPE, game)[kind]["isUp4"] == 1
    counter(game, kind, num4=9)
    result = simulate.do_gacha(SCOPE, kind, game=game, count=1, rng=FakeRng())
    assert result["list"][0]["name"] in simulate.get_pool(kind, game=game)["up4"]
    assert simulate.load_user(SCOPE, game)[kind]["isUp4"] == 0


def test_fate_one_point_switch_cancel_and_new_period(sim_data):
    pool = simulate.get_pool("weapon")
    simulate.toggle_bing(SCOPE, 2)
    counter("gs", "weapon", num5=79)
    result = simulate.do_gacha(SCOPE, "weapon", count=1, rng=FakeRng())
    assert result["lifeNum"] == 1
    counter("gs", "weapon", num5=79, isUp5=1)
    result = simulate.do_gacha(SCOPE, "weapon", count=1, rng=FakeRng())
    assert result["list"][0]["name"] == pool["up5"][1]
    assert result["list"][0]["isBing"]
    assert simulate.load_user(SCOPE)["weapon"]["isUp5"] == 0
    counter("gs", "weapon", lifeNum=1)
    simulate.toggle_bing(SCOPE, 2)  # 重复选中原目标不清进度
    assert simulate.load_user(SCOPE)["weapon"]["lifeNum"] == 1
    simulate.toggle_bing(SCOPE, 1)
    assert simulate.load_user(SCOPE)["weapon"]["lifeNum"] == 0
    simulate.toggle_bing(SCOPE, 0)
    counter("gs", "weapon", num5=79)
    assert simulate.do_gacha(SCOPE, "weapon", count=1, rng=FakeRng())["lifeNum"] == 0
    counter("gs", "weapon", num5=15, isUp5=1, lifeNum=1, type=2)
    new = deepcopy(pool)
    new["id"] = "next-period"
    simulate.do_gacha(SCOPE, "weapon", count=1, pool=new, rng=FakeRng())
    state = simulate.load_user(SCOPE)["weapon"]
    assert (state["num5"], state["isUp5"], state["lifeNum"], state["type"]) == (16, 1, 0, 0)


def test_toggle_bing_cycle(sim_data):
    for selected in (1, 2, 0, 1):
        simulate.toggle_bing(SCOPE)
        assert simulate.load_user(SCOPE)["weapon"]["type"] == selected
    with pytest.raises(ValueError):
        simulate.toggle_bing(SCOPE, 3)


def test_game_group_and_scope_isolation(sim_data):
    simulate.do_gacha(SCOPE, "role", rng=FakeRng())
    simulate.do_gacha(SCOPE, "role", game="sr", rng=FakeRng())
    simulate.do_gacha(SCOPE, "role", game="zzz", rng=FakeRng())
    for game in ("gs", "sr", "zzz"):
        assert simulate.load_user(SCOPE, game)["role"]["num5"] == 10
        assert simulate.load_user(SCOPE, game)["weapon"]["num5"] == 0
        assert simulate.do_gacha(SCOPE, "weapon", game=game)["code"] == "limit"
    assert simulate.do_gacha("private:67890", "role", rng=FakeRng())["code"] == "ok"
    collab = next(p for p in sim_data["sr"]["pools"] if p["group"] == "collab_role")
    simulate.do_gacha(SCOPE, "role3", game="sr", is_master=True, pool=collab, rng=FakeRng())
    state = simulate.load_user(SCOPE, "sr")
    assert state["collab_role"]["num5"] == state["role"]["num5"] == 10


def test_single_draw_and_no_budget_overshoot(sim_data):
    result = simulate.do_gacha(SCOPE, "role", count=1, rng=FakeRng())
    assert len(result["list"]) == 1 and result["list"][0]["star"] == 3
    state = simulate.load_user(SCOPE)
    assert state["today"]["num"] == state["role"]["num5"] == state["role"]["num4"] == 1
    before = deepcopy(state)
    assert simulate.do_gacha(SCOPE, "role", rng=FakeRng())["code"] == "limit"
    assert simulate.load_user(SCOPE) == before
    for _ in range(9):
        simulate.do_gacha(SCOPE, "role", count=1, rng=FakeRng())
    assert simulate.do_gacha(SCOPE, "role", count=1)["code"] == "limit"
    assert simulate.do_gacha(SCOPE, "role", is_master=True)["code"] == "ok"


def test_daily_reset_at_four_in_china(sim_data, monkeypatch):
    monkeypatch.setattr(simulate, "_now", lambda: sim_pools.timestamp("2026-09-09 00:00:00"))
    simulate.do_gacha(SCOPE, "role", rng=FakeRng())
    assert simulate.load_user(SCOPE)["today"]["expire"] == sim_pools.timestamp("2026-09-09 04:00:00")
    monkeypatch.setattr(simulate, "_now", lambda: sim_pools.timestamp("2026-09-09 03:59:59"))
    assert simulate.do_gacha(SCOPE, "role")["code"] == "limit"
    monkeypatch.setattr(simulate, "_now", lambda: sim_pools.timestamp("2026-09-09 04:00:00"))
    assert simulate.do_gacha(SCOPE, "role", rng=FakeRng())["code"] == "ok"
    assert simulate.load_user(SCOPE)["role"]["num5"] == 20


def test_old_genshin_state_migration(sim_data):
    old = simulate._new_user()
    old["role"].update(num5=60, isUp5=1)
    old["weapon"].update(num5=55, isUp5=1, lifeNum=2, type=2)
    store.write_sim_state(SCOPE, old)
    simulate.do_gacha(SCOPE, "role2", count=1, rng=FakeRng())
    simulate.do_gacha(SCOPE, "weapon", count=1, rng=FakeRng())
    state = store.read_sim_state(SCOPE)
    assert state["role"]["num5"] == 61 and state["role"]["isUp5"] == 1
    assert state["weapon"]["num5"] == 56 and state["weapon"]["isUp5"] == 1
    assert state["weapon"]["lifeNum"] == state["weapon"]["type"] == 0


def test_invalid_pool_or_expired_never_consumes_draws(sim_data):
    before = store.read_sim_state(SCOPE)
    for kind in ("role99", "weapon2", "role0", "invalid"):
        with pytest.raises(ValueError):
            simulate.do_gacha(SCOPE, kind)
    expired = deepcopy(simulate.get_pool("role"))
    expired["end"] = 1
    with pytest.raises(ValueError, match="已经结束"):
        simulate.do_gacha(SCOPE, "role", pool=expired)
    assert store.read_sim_state(SCOPE) == before


def test_concurrent_draws_cannot_bypass_limit(sim_data):
    def draw(_):
        return simulate.do_gacha(SCOPE, "role", rng=FakeRng())
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(draw, range(4)))
    assert sum(r["code"] == "ok" for r in results) == 1
    assert simulate.load_user(SCOPE)["today"]["num"] == 10


def test_result_sorting_and_game_item_types(sim_data):
    for game in ("gs", "sr", "zzz"):
        counter(game, "role", num5=89, num4=8)
        result = simulate.do_gacha(SCOPE, "role", game=game, rng=FakeRng())
        stars = [v["star"] for v in result["list"]]
        assert stars == [5, 4] + [3] * 8
        assert result["list"][0]["type"] == "role"
        assert result["game"] == game
