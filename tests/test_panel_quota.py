"""面板次数 / 免费额度 / 付费确认 / 限流联动的单元测试。"""
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
    panel_quota._PENDING.clear()
    return tmp_path


def make_event(group_id: int | None = 571494255, user_id: str = "10001"):
    return SimpleNamespace(
        group_id=group_id,
        get_user_id=lambda: user_id,
        sender=SimpleNamespace(card="", nickname="测试员"),
    )


def make_config(free: int = 2, limit: int = 4, cost: int = 10, timeout: int = 30):
    return SimpleNamespace(
        miao_panel_free_per_hour=free,
        miao_panel_user_hourly_limit=limit,
        miao_panel_affection_cost=cost,
        miao_panel_confirm_timeout=timeout,
    )


def use_mantou(monkeypatch, fake: FakeMantou | None):
    monkeypatch.setattr(panel_quota, "_API", fake if fake is not None else False)


async def test_private_is_free(monkeypatch):
    fake = FakeMantou(draws=0, affection=0)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(group_id=None), make_config())
    assert check.allowed is True
    assert fake.consumed == 0
    assert fake.spent == []
    assert "私聊" in await panel_quota.panel_status(make_event(group_id=None), make_config())


async def test_superuser_bypasses_everything(monkeypatch):
    fake = FakeMantou(draws=0, affection=0)
    use_mantou(monkeypatch, fake)
    monkeypatch.setattr(panel_quota, "is_superuser", lambda user_id: True)
    for _ in range(10):
        assert (await panel_quota.authorize_panel_call(make_event(), make_config())).allowed is True
    assert fake.consumed == 0
    assert fake.spent == []
    assert store.panel_user_calls(571494255, "10001") == 0
    assert "SUPERUSER" in await panel_quota.panel_status(make_event(), make_config())


async def test_first_two_are_free(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(2):
        check = await panel_quota.authorize_panel_call(make_event(), make_config())
        assert check.allowed is True
        assert check.paid is False
        assert check.used_draw is False
    assert fake.affection == 50
    assert store.panel_user_calls(571494255, "10001") == 2
    assert store.panel_user_free_used(571494255, "10001") == 2


async def test_third_call_needs_confirmation(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(2):
        await panel_quota.authorize_panel_call(make_event(), make_config())
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is False
    assert check.needs_confirm is True
    assert "消耗 10 点好感度" in check.message
    assert "回复「确认」" in check.message
    assert fake.affection == 50
    assert store.panel_user_calls(571494255, "10001") == 2


async def test_confirmed_third_and_fourth_call_charge_affection(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(2):
        await panel_quota.authorize_panel_call(make_event(), make_config())
    for _ in range(2):
        check = await panel_quota.authorize_panel_call(make_event(), make_config(), confirmed=True)
        assert check.allowed is True
        assert check.paid is True
    assert fake.affection == 30
    assert fake.spent == [(10, "nonebot_plugin_miao:panel"), (10, "nonebot_plugin_miao:panel")]
    assert store.panel_user_calls(571494255, "10001") == 4

    blocked = await panel_quota.authorize_panel_call(make_event(), make_config(), confirmed=True)
    assert blocked.allowed is False
    assert "已查看 4 次" in blocked.message
    assert fake.affection == 30


async def test_insufficient_affection_denies_without_confirm(monkeypatch):
    fake = FakeMantou(draws=0, affection=5)
    use_mantou(monkeypatch, fake)
    for _ in range(2):
        await panel_quota.authorize_panel_call(make_event(), make_config())
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is False
    assert check.needs_confirm is False
    assert "好感度不足 10 点" in check.message
    assert fake.spent == []
    assert store.panel_user_calls(571494255, "10001") == 2


async def test_wheel_draw_is_consumed_first_without_using_free(monkeypatch):
    fake = FakeMantou(draws=2, affection=50)
    use_mantou(monkeypatch, fake)
    first = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert first.allowed is True and first.used_draw is True
    assert fake.draws == 1
    assert store.panel_user_free_used(571494255, "10001") == 0
    assert store.panel_user_calls(571494255, "10001") == 1

    second = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert second.used_draw is True
    third = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert third.allowed is True and third.paid is False
    assert store.panel_user_free_used(571494255, "10001") == 1


async def test_draws_still_count_toward_hourly_limit(monkeypatch):
    fake = FakeMantou(draws=10, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(4):
        assert (await panel_quota.authorize_panel_call(make_event(), make_config())).allowed is True
    blocked = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert blocked.allowed is False
    assert "已查看 4 次" in blocked.message


async def test_limit_is_per_user(monkeypatch):
    fake = FakeMantou(draws=10, affection=50)
    use_mantou(monkeypatch, fake)
    config = make_config(limit=1)
    assert (await panel_quota.authorize_panel_call(make_event(user_id="10001"), config)).allowed is True
    assert (await panel_quota.authorize_panel_call(make_event(user_id="10001"), config)).allowed is False
    assert (await panel_quota.authorize_panel_call(make_event(user_id="10002"), config)).allowed is True


async def test_missing_mantou_plugin_is_free(monkeypatch):
    use_mantou(monkeypatch, None)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is True
    assert "未加载馒头好感度插件" in await panel_quota.panel_status(make_event(), make_config())


async def test_panel_status(monkeypatch):
    fake = FakeMantou(draws=3, affection=20)
    use_mantou(monkeypatch, fake)
    await panel_quota.authorize_panel_call(make_event(), make_config())
    status = await panel_quota.panel_status(make_event(), make_config())
    assert "转盘面板次数：2 次（优先消耗）" in status
    assert "本小时免费次数：2/2 次" in status
    assert "本小时已查看：1/4 次" in status
    assert "当前好感度：20 点" in status
    assert "需回复「确认」" in status


def test_pending_lifecycle():
    event = make_event()
    panel_quota.register_pending(
        event,
        avatar={"id": "10000051", "name": "优菈"},
        game="gs",
        uid="800055548",
        show_dmg=True,
        dmg_idx=2,
    )
    assert panel_quota.has_pending(571494255, "10001", 30) is True
    pending = panel_quota.pop_pending(571494255, "10001", 30)
    assert pending is not None
    assert pending.avatar["name"] == "优菈"
    assert pending.show_dmg is True
    assert pending.dmg_idx == 2
    assert panel_quota.has_pending(571494255, "10001", 30) is False


def test_pending_expires():
    event = make_event()
    pending = panel_quota.register_pending(
        event,
        avatar={},
        game="gs",
        uid="800055548",
        show_dmg=False,
        dmg_idx=None,
    )
    pending.created_at -= 31
    assert panel_quota.has_pending(571494255, "10001", 30) is False
    assert panel_quota.pop_pending(571494255, "10001", 30) is None


def test_clear_pending():
    event = make_event()
    panel_quota.register_pending(
        event, avatar={}, game="gs", uid="800055548", show_dmg=False, dmg_idx=None
    )
    assert panel_quota.clear_pending(571494255, "10001") is not None
    assert panel_quota.has_pending(571494255, "10001", 30) is False


def test_usage_window_and_legacy_data(tmp_path):
    path = tmp_path / "panel_usage.json"
    old = time.time() - 3700
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "groups": {"571494255": {"10001": [{"t": old, "k": "free"}]}},
            }
        ),
        encoding="utf-8",
    )
    assert store.panel_user_calls(571494255, "10001") == 0
    assert store.panel_user_wait(571494255, "10001", limit=4) == 0

    # 旧版按群的纯时间戳结构应被忽略，不参与新规则
    path.write_text(json.dumps({"571494255": [time.time()]}), encoding="utf-8")
    assert store.panel_user_calls(571494255, "10001") == 0


def test_panel_enabled_switch(monkeypatch):
    config_on = SimpleNamespace(miao_panel_enabled=True)
    config_off = SimpleNamespace(miao_panel_enabled=False)
    panel_quota.set_panel_enabled(None)
    assert panel_quota.panel_enabled(config_on) is True
    assert panel_quota.panel_enabled(config_off) is False

    panel_quota.set_panel_enabled(False)
    assert panel_quota.panel_enabled(config_on) is False
    assert panel_quota.panel_enabled_source() == "运行时覆盖"

    panel_quota.set_panel_enabled(True)
    assert panel_quota.panel_enabled(config_off) is True

    panel_quota.set_panel_enabled(None)
    assert panel_quota.panel_enabled_source() == "配置文件"
    assert panel_quota.panel_enabled(config_on) is True


def test_disable_cancels_pending():
    event = make_event()
    panel_quota.register_pending(
        event, avatar={}, game="gs", uid="800055548", show_dmg=False, dmg_idx=None
    )
    assert panel_quota.set_panel_enabled(False) == 1
    assert panel_quota.has_pending(571494255, "10001", 30) is False
    panel_quota.set_panel_enabled(None)


def test_enable_keeps_pending():
    event = make_event()
    panel_quota.register_pending(
        event, avatar={}, game="gs", uid="800055548", show_dmg=False, dmg_idx=None
    )
    assert panel_quota.set_panel_enabled(True) == 0
    assert panel_quota.has_pending(571494255, "10001", 30) is True
    panel_quota.set_panel_enabled(None)


async def test_ensure_panel_enabled_gate(monkeypatch):
    from nonebot_plugin_miao.commands import common

    class FakeMatcher:
        async def finish(self, message: str = ""):
            raise RuntimeError(str(message))

    monkeypatch.setattr(common, "panel_enabled", lambda: False)
    with pytest.raises(RuntimeError, match="面板功能已关闭"):
        await common.ensure_panel_enabled(FakeMatcher())

    monkeypatch.setattr(common, "panel_enabled", lambda: True)
    assert await common.ensure_panel_enabled(FakeMatcher()) is None
