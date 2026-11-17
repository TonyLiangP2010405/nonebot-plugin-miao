"""面板次数 / 好感度 / 本群限流联动的单元测试。"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from nonebot_plugin_miao.core import panel_quota, store


class FakeMantou:
    def __init__(self, draws: int = 0, affection: int = 0):
        self.draws = draws
        self.affection = affection
        self.consumed = 0
        self.spent: list[tuple[int, str]] = []

    async def get_panel_draws(self, group_id, user_id) -> int:
        return self.draws

    async def consume_panel_draw(self, group_id, user_id, amount: int = 1) -> int | None:
        if self.draws < amount:
            return None
        self.draws -= amount
        self.consumed += 1
        return self.draws

    async def get_affection(self, group_id, user_id) -> int:
        return self.affection

    async def spend_affection(
        self, group_id, user_id, amount: int, *, nickname: str = "", source: str = ""
    ) -> tuple[bool, int]:
        if self.affection < amount:
            return False, self.affection
        self.affection -= amount
        self.spent.append((amount, source))
        return True, self.affection


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)
    return tmp_path


def make_event(group_id: int | None = 123456, user_id: str = "10001"):
    return SimpleNamespace(
        group_id=group_id,
        get_user_id=lambda: user_id,
        sender=SimpleNamespace(card="", nickname="测试员"),
    )


def make_config(limit: int = 5, cost: int = 10):
    return SimpleNamespace(miao_panel_group_hourly_limit=limit, miao_panel_affection_cost=cost)


def use_mantou(monkeypatch, fake: FakeMantou | None):
    monkeypatch.setattr(panel_quota, "_API", fake if fake is not None else False)


async def test_private_is_free(monkeypatch):
    fake = FakeMantou(draws=0, affection=0)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(group_id=None), make_config())
    assert check.allowed is True
    assert fake.consumed == 0
    assert fake.spent == []
    assert await panel_quota.panel_status(make_event(group_id=None), make_config()) == (
        "私聊查看角色面板免费，不消耗面板次数。"
    )


async def test_superuser_bypasses_everything(monkeypatch):
    fake = FakeMantou(draws=0, affection=0)
    use_mantou(monkeypatch, fake)
    monkeypatch.setattr(panel_quota, "is_superuser", lambda user_id: True)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is True
    assert fake.consumed == 0
    assert fake.spent == []
    assert store.panel_group_used(123456) == 0
    assert "SUPERUSER" in await panel_quota.panel_status(make_event(), make_config())


async def test_draw_is_consumed_first(monkeypatch):
    fake = FakeMantou(draws=2, affection=50)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is True
    assert check.used_draw is True
    assert check.paid is False
    assert fake.draws == 1
    assert fake.affection == 50
    assert store.panel_group_used(123456) == 1


async def test_affection_fallback_costs_ten(monkeypatch):
    fake = FakeMantou(draws=0, affection=15)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config(cost=10))
    assert check.allowed is True
    assert check.paid is True
    assert check.used_draw is False
    assert fake.affection == 5
    assert fake.spent == [(10, "nonebot_plugin_miao:panel")]
    assert store.panel_group_used(123456) == 1


async def test_insufficient_draws_and_affection(monkeypatch):
    fake = FakeMantou(draws=0, affection=5)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config(cost=10))
    assert check.allowed is False
    assert "好感度不足 10 点" in check.message
    assert "当前 5" in check.message
    assert fake.spent == []
    assert store.panel_group_used(123456) == 0


async def test_group_hourly_limit(monkeypatch):
    fake = FakeMantou(draws=10, affection=0)
    use_mantou(monkeypatch, fake)
    config = make_config(limit=5)
    for _ in range(5):
        assert (await panel_quota.authorize_panel_call(make_event(), config)).allowed is True
    blocked = await panel_quota.authorize_panel_call(make_event(), config)
    assert blocked.allowed is False
    assert "每小时最多查看 5 次" in blocked.message
    assert fake.consumed == 5
    assert store.panel_group_used(123456) == 5


async def test_limit_is_per_group(monkeypatch):
    fake = FakeMantou(draws=10, affection=0)
    use_mantou(monkeypatch, fake)
    config = make_config(limit=1)
    assert (await panel_quota.authorize_panel_call(make_event(111), config)).allowed is True
    assert (await panel_quota.authorize_panel_call(make_event(111), config)).allowed is False
    assert (await panel_quota.authorize_panel_call(make_event(222), config)).allowed is True


async def test_missing_mantou_plugin_is_free(monkeypatch):
    use_mantou(monkeypatch, None)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is True
    assert "未加载馒头好感度插件" in await panel_quota.panel_status(make_event(), make_config())


async def test_panel_status(monkeypatch):
    fake = FakeMantou(draws=3, affection=20)
    use_mantou(monkeypatch, fake)
    store.record_panel_group_call(123456)
    status = await panel_quota.panel_status(make_event(), make_config(limit=5, cost=10))
    assert "你的角色面板次数：3 次" in status
    assert "当前好感度：20 点（次数用完时每次消耗 10 点）" in status
    assert "本群本小时已查看：1/5 次" in status
    assert "5% 概率" in status


def test_usage_window_expires(tmp_path):
    path = tmp_path / "panel_usage.json"
    old = time.time() - 3700
    path.write_text(json.dumps({"123456": [old, old]}), encoding="utf-8")
    assert store.panel_group_used(123456) == 0
    assert store.panel_group_wait(123456, limit=5) == 0
