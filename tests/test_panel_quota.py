"""面板次数 / 免费额度 / 付费确认 / 限流联动的单元测试。"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nonebot_plugin_miao.core import panel_quota, store


class FakeMantou:
    def __init__(self, draws: int = 0, affection: int = 0, rank_draws: int = 0):
        self.draws = draws
        self.rank_draws = rank_draws
        self.affection = affection
        self.consumed = 0
        self.rank_consumed = 0
        self.spent: list[tuple[int, str]] = []

    async def get_panel_draws(self, group_id, user_id) -> int:
        return self.draws

    async def consume_panel_draw(self, group_id, user_id, amount: int = 1) -> int | None:
        if self.draws < amount:
            return None
        self.draws -= amount
        self.consumed += 1
        return self.draws

    async def get_panel_rank_draws(self, group_id, user_id) -> int:
        return self.rank_draws

    async def consume_panel_rank_draw(
        self, group_id, user_id, amount: int = 1
    ) -> int | None:
        if self.rank_draws < amount:
            return None
        self.rank_draws -= amount
        self.rank_consumed += 1
        return self.rank_draws

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


_TZ = timezone(timedelta(hours=8))


def use_clock(monkeypatch, hour: int = 12, minute: int = 0):
    """固定面板判断用的当前时间，默认开放时段内 12:00。"""
    fixed = datetime(2026, 1, 1, hour, minute, tzinfo=_TZ)
    monkeypatch.setattr(panel_quota, "_local_now", lambda now=None: now or fixed)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)
    panel_quota._PENDING.clear()
    use_clock(monkeypatch)
    return tmp_path


def make_event(group_id: int | None = 571494255, user_id: str = "10001"):
    return SimpleNamespace(
        group_id=group_id,
        get_user_id=lambda: user_id,
        sender=SimpleNamespace(card="", nickname="测试员"),
    )


def make_config(
    free: int = 1,
    limit: int = 2,
    cost: int = 10,
    timeout: int = 30,
    open_hour: int = 8,
    close_hour: int = 17,
    offhour_cost: int = 50,
):
    return SimpleNamespace(
        miao_panel_free_per_hour=free,
        miao_panel_user_hourly_limit=limit,
        miao_panel_affection_cost=cost,
        miao_panel_offhour_affection_cost=offhour_cost,
        miao_panel_confirm_timeout=timeout,
        miao_panel_open_hour=open_hour,
        miao_panel_close_hour=close_hour,
        miao_panel_update_interval=60,
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


async def test_first_call_is_free(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is True
    assert check.paid is False
    assert check.used_draw is False
    assert fake.affection == 50
    assert store.panel_user_calls(571494255, "10001") == 1
    assert store.panel_user_free_used(571494255, "10001") == 1

    second = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert second.allowed is False
    assert second.needs_confirm is True


async def test_second_call_needs_confirmation(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    await panel_quota.authorize_panel_call(make_event(), make_config())
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is False
    assert check.needs_confirm is True
    assert "消耗 10 点好感度" in check.message
    assert "回复「确认」" in check.message
    assert fake.affection == 50
    assert store.panel_user_calls(571494255, "10001") == 1


async def test_confirmed_second_call_charges_affection(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    await panel_quota.authorize_panel_call(make_event(), make_config())
    check = await panel_quota.authorize_panel_call(make_event(), make_config(), confirmed=True)
    assert check.allowed is True
    assert check.paid is True
    assert fake.affection == 40
    assert fake.spent == [(10, "nonebot_plugin_miao:panel")]
    assert store.panel_user_calls(571494255, "10001") == 2

    blocked = await panel_quota.authorize_panel_call(make_event(), make_config(), confirmed=True)
    assert blocked.allowed is False
    assert "已查看 2 次" in blocked.message
    assert fake.affection == 40


async def test_insufficient_affection_denies_without_confirm(monkeypatch):
    fake = FakeMantou(draws=0, affection=5)
    use_mantou(monkeypatch, fake)
    await panel_quota.authorize_panel_call(make_event(), make_config())
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is False
    assert check.needs_confirm is False
    assert "好感度不足 10 点" in check.message
    assert fake.spent == []
    assert store.panel_user_calls(571494255, "10001") == 1


async def test_wheel_draw_is_consumed_first_without_using_free_or_limit(monkeypatch):
    fake = FakeMantou(draws=2, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(2):
        check = await panel_quota.authorize_panel_call(make_event(), make_config())
        assert check.allowed is True and check.used_draw is True
    assert fake.draws == 0
    assert store.panel_user_free_used(571494255, "10001") == 0
    assert store.panel_user_calls(571494255, "10001") == 0

    free = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert free.allowed is True and free.used_draw is False
    assert store.panel_user_free_used(571494255, "10001") == 1
    assert store.panel_user_calls(571494255, "10001") == 1


async def test_wheel_draws_are_not_limited(monkeypatch):
    fake = FakeMantou(draws=10, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(10):
        assert (await panel_quota.authorize_panel_call(make_event(), make_config())).allowed is True
    assert store.panel_user_calls(571494255, "10001") == 0
    # 转盘次数用完后，仍然有本小时的 1 次免费
    assert (await panel_quota.authorize_panel_call(make_event(), make_config())).allowed is True


async def test_has_panel_draws(monkeypatch):
    fake = FakeMantou(draws=1, rank_draws=0)
    use_mantou(monkeypatch, fake)
    assert await panel_quota.has_panel_draws(make_event()) is True
    assert await panel_quota.has_panel_draws(make_event(), feature="rank") is False
    assert await panel_quota.has_panel_draws(make_event(group_id=None)) is False

    use_mantou(monkeypatch, FakeMantou(draws=0, rank_draws=2))
    assert await panel_quota.has_panel_draws(make_event()) is False
    assert await panel_quota.has_panel_draws(make_event(), feature="rank") is True


async def test_offhour_paid_requires_confirm(monkeypatch):
    use_clock(monkeypatch, hour=22)
    fake = FakeMantou(draws=0, affection=100)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is False
    assert check.needs_confirm is True
    assert check.cost == 50
    assert "非面板开放时段" in check.message
    assert "50 点好感度" in check.message
    assert "回复「确认」" in check.message
    assert fake.affection == 100
    assert store.panel_user_calls(571494255, "10001") == 0
    assert store.panel_user_free_used(571494255, "10001") == 0

    done = await panel_quota.authorize_panel_call(
        make_event(), make_config(), confirmed=True, quoted_cost=check.cost
    )
    assert done.allowed is True and done.paid is True
    assert fake.affection == 50
    assert fake.spent == [(50, "nonebot_plugin_miao:panel_offhour")]
    # 非开放时段付费不占免费额度 / 本小时次数
    assert store.panel_user_calls(571494255, "10001") == 0
    assert store.panel_user_free_used(571494255, "10001") == 0


async def test_offhour_insufficient_affection(monkeypatch):
    use_clock(monkeypatch, hour=22)
    fake = FakeMantou(draws=0, affection=40)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is False
    assert check.needs_confirm is False
    assert "需消耗 50 点好感度" in check.message
    assert "当前 40" in check.message
    assert fake.spent == []


async def test_offhour_draw_still_priority(monkeypatch):
    use_clock(monkeypatch, hour=22)
    fake = FakeMantou(draws=1, affection=100)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(), make_config())
    assert check.allowed is True and check.used_draw is True
    assert fake.affection == 100
    assert fake.draws == 0


async def test_offhour_private_still_closed(monkeypatch):
    use_clock(monkeypatch, hour=22)
    fake = FakeMantou(affection=100)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(make_event(group_id=None), make_config())
    assert check.allowed is False
    assert "面板功能每天" in check.message


async def test_offhour_disabled_by_zero_cost(monkeypatch):
    use_clock(monkeypatch, hour=22)
    fake = FakeMantou(affection=100)
    use_mantou(monkeypatch, fake)
    check = await panel_quota.authorize_panel_call(
        make_event(), make_config(offhour_cost=0)
    )
    assert check.allowed is False
    assert check.needs_confirm is False
    assert fake.spent == []


async def test_offhour_rank_uses_rank_source(monkeypatch):
    use_clock(monkeypatch, hour=22)
    fake = FakeMantou(draws=0, rank_draws=0, affection=100)
    use_mantou(monkeypatch, fake)
    prompt = await panel_quota.authorize_panel_call(
        make_event(), make_config(), feature="rank"
    )
    assert prompt.needs_confirm is True and prompt.cost == 50
    assert "群面板排名" in prompt.message
    done = await panel_quota.authorize_panel_call(
        make_event(), make_config(), confirmed=True, feature="rank", quoted_cost=prompt.cost
    )
    assert done.allowed is True and done.paid is True
    assert fake.spent == [(50, "nonebot_plugin_miao:panel_rank_offhour")]


async def test_offhour_quote_not_increased_after_close(monkeypatch):
    # 开放时段报价 10，确认时已过 17:00，仍按 10 扣
    use_clock(monkeypatch, hour=17, minute=10)
    fake = FakeMantou(affection=100)
    use_mantou(monkeypatch, fake)
    done = await panel_quota.authorize_panel_call(
        make_event(), make_config(), confirmed=True, quoted_cost=10
    )
    assert done.allowed is True and done.paid is True
    assert fake.affection == 90


async def test_offhour_quote_not_charged_when_open(monkeypatch):
    # 非开放时段报价 50，确认时已到开放时段，优先用免费次数
    use_clock(monkeypatch, hour=12)
    fake = FakeMantou(affection=100)
    use_mantou(monkeypatch, fake)
    done = await panel_quota.authorize_panel_call(
        make_event(), make_config(), confirmed=True, quoted_cost=50
    )
    assert done.allowed is True and done.paid is False
    assert fake.affection == 100


def test_panel_offhour_access(monkeypatch):
    use_mantou(monkeypatch, FakeMantou())
    assert panel_quota.panel_offhour_access(make_event()) is True
    assert panel_quota.panel_offhour_access(make_event(group_id=None)) is False
    assert panel_quota.panel_offhour_access(make_event(user_id="99999")) is True

    monkeypatch.setattr(panel_quota, "is_superuser", lambda user_id: True)
    assert panel_quota.panel_offhour_access(make_event()) is True
    monkeypatch.setattr(panel_quota, "is_superuser", lambda user_id: False)

    monkeypatch.setattr(panel_quota, "_plugin_config", lambda: make_config(offhour_cost=0))
    assert panel_quota.panel_offhour_access(make_event()) is False
    monkeypatch.setattr(panel_quota, "_plugin_config", lambda: make_config())

    use_mantou(monkeypatch, None)
    assert panel_quota.panel_offhour_access(make_event()) is False


async def test_rank_quota_is_independent_from_detail(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    assert (
        await panel_quota.authorize_panel_call(make_event(), make_config(), feature="rank")
    ).allowed is True
    assert (
        await panel_quota.authorize_panel_call(make_event(), make_config(), feature="rank")
    ).needs_confirm is True
    assert (
        await panel_quota.authorize_panel_call(make_event(), make_config())
    ).allowed is True
    assert (
        await panel_quota.authorize_panel_call(make_event(), make_config())
    ).needs_confirm is True
    assert store.panel_user_calls(571494255, "10001", feature="rank") == 1
    assert store.panel_user_calls(571494255, "10001", feature="detail") == 1


async def test_rank_draw_does_not_consume_detail_draw(monkeypatch):
    fake = FakeMantou(draws=0, rank_draws=3, affection=50)
    use_mantou(monkeypatch, fake)
    for _ in range(3):
        check = await panel_quota.authorize_panel_call(
            make_event(), make_config(), feature="rank"
        )
        assert check.allowed is True and check.used_draw is True
    assert fake.rank_consumed == 3
    assert fake.consumed == 0
    assert store.panel_user_calls(571494255, "10001", feature="rank") == 0
    assert (
        await panel_quota.authorize_panel_call(make_event(), make_config())
    ).allowed is True


async def test_rank_paid_uses_rank_source(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
    use_mantou(monkeypatch, fake)
    await panel_quota.authorize_panel_call(make_event(), make_config(), feature="rank")
    check = await panel_quota.authorize_panel_call(
        make_event(), make_config(), confirmed=True, feature="rank"
    )
    assert check.allowed is True and check.paid is True
    assert fake.spent == [(10, "nonebot_plugin_miao:panel_rank")]
    assert store.panel_user_calls(571494255, "10001", feature="rank") == 2


async def test_limit_is_per_user(monkeypatch):
    fake = FakeMantou(draws=0, affection=50)
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
    fake = FakeMantou(draws=3, rank_draws=2, affection=20)
    use_mantou(monkeypatch, fake)
    await panel_quota.authorize_panel_call(make_event(), make_config())
    status = await panel_quota.panel_status(make_event(), make_config())
    assert "面板开放时间：08:00–17:00" in status
    assert "角色面板：转盘次数 2 次" in status
    assert "角色面板：免费剩余 1/1 次，已查看 0/2 次" in status
    assert "群面板排名：转盘次数 2 次" in status
    assert "群面板排名：免费剩余 1/1 次，已查看 0/2 次" in status
    assert "当前好感度：20 点" in status
    assert "需回复「确认」" in status
    assert "非开放时段每次消耗 50 点好感度" in status
    assert "不受开放时段限制" in status


def test_panel_time_window():
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=8))
    config = make_config(open_hour=8, close_hour=17)
    assert panel_quota.panel_time_allowed(config, datetime(2026, 1, 1, 8, 0, tzinfo=tz))
    assert panel_quota.panel_time_allowed(config, datetime(2026, 1, 1, 16, 59, tzinfo=tz))
    assert not panel_quota.panel_time_allowed(config, datetime(2026, 1, 1, 7, 59, tzinfo=tz))
    assert not panel_quota.panel_time_allowed(config, datetime(2026, 1, 1, 17, 0, tzinfo=tz))

    overnight = make_config(open_hour=22, close_hour=6)
    assert panel_quota.panel_time_allowed(overnight, datetime(2026, 1, 1, 23, 0, tzinfo=tz))
    assert panel_quota.panel_time_allowed(overnight, datetime(2026, 1, 1, 5, 59, tzinfo=tz))
    assert not panel_quota.panel_time_allowed(overnight, datetime(2026, 1, 1, 12, 0, tzinfo=tz))

    always = make_config(open_hour=0, close_hour=0)
    assert panel_quota.panel_time_allowed(always, datetime(2026, 1, 1, 3, 0, tzinfo=tz))
    assert "全天开放" in panel_quota.panel_window_text(always)


def test_update_panel_hourly_limit():
    config = make_config()
    assert panel_quota.update_wait("10001", "gs", config) == 0
    panel_quota.mark_update("10001", "gs", config)
    wait = panel_quota.update_wait("10001", "gs", config)
    assert 3500 <= wait <= 3600
    assert "每小时只能使用 1 次" in panel_quota.update_limit_message(wait)
    assert "60 分钟" in panel_quota.update_limit_message(wait)
    # 另一个游戏单独计时，另一个群友不受影响
    assert panel_quota.update_wait("10001", "sr", config) == 0
    assert panel_quota.update_wait("10002", "gs", config) == 0


def test_update_panel_limit_can_be_disabled():
    config = make_config()
    config.miao_panel_update_interval = 0
    panel_quota.mark_update("10001", "gs", config)
    assert panel_quota.update_wait("10001", "gs", config) == 0


def test_panel_closed_message():
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=8))
    config = make_config(open_hour=8, close_hour=17)
    morning = panel_quota.panel_closed_message(config, datetime(2026, 1, 1, 7, 0, tzinfo=tz))
    assert "今天 08:00 后" in morning
    assert "转盘抽到的面板 / 群排名次数不受时段限制" in morning
    evening = panel_quota.panel_closed_message(config, datetime(2026, 1, 1, 18, 0, tzinfo=tz))
    assert "明天 08:00 后" in evening


def _detail_payload() -> dict:
    return {
        "avatar": {"id": "10000051", "name": "优菈"},
        "game": "gs",
        "uid": "800055548",
        "show_dmg": True,
        "dmg_idx": 2,
    }


def test_pending_lifecycle():
    event = make_event()
    panel_quota.register_pending(event, feature="detail", payload=_detail_payload())
    assert panel_quota.has_pending(571494255, "10001", 30) is True
    pending = panel_quota.pop_pending(571494255, "10001", 30)
    assert pending is not None
    assert pending.feature == "detail"
    assert pending.payload["avatar"]["name"] == "优菈"
    assert pending.payload["show_dmg"] is True
    assert pending.payload["dmg_idx"] == 2
    assert panel_quota.has_pending(571494255, "10001", 30) is False


def test_pending_records_quoted_cost():
    pending = panel_quota.register_pending(make_event(), cost=50)
    assert pending.cost == 50
    assert panel_quota.register_pending(make_event(), cost=-5).cost == 0


def test_rank_pending_lifecycle():
    event = make_event()
    panel_quota.register_pending(
        event, feature="rank", payload={"game": "gs", "char": "优菈", "mode": "dmg"}
    )
    pending = panel_quota.pop_pending(571494255, "10001", 30)
    assert pending is not None
    assert pending.feature == "rank"
    assert pending.payload == {"game": "gs", "char": "优菈", "mode": "dmg"}


def test_pending_expires():
    event = make_event()
    pending = panel_quota.register_pending(
        event, feature="detail", payload={"avatar": {}, "game": "gs", "uid": "800055548"}
    )
    pending.created_at -= 31
    assert panel_quota.has_pending(571494255, "10001", 30) is False
    assert panel_quota.pop_pending(571494255, "10001", 30) is None


def test_clear_pending():
    event = make_event()
    panel_quota.register_pending(event, feature="detail", payload=_detail_payload())
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

    # 旧版把转盘次数记进额度；新规则应忽略 draw，只保留 free/paid
    recent = time.time()
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "groups": {
                    "571494255": {
                        "10001": [
                            {"t": recent, "k": "draw"},
                            {"t": recent, "k": "free"},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert store.panel_user_calls(571494255, "10001") == 1
    assert store.panel_user_free_used(571494255, "10001") == 1
    assert store.panel_user_calls(571494255, "10001", feature="rank") == 0

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
    panel_quota.register_pending(event, feature="detail", payload=_detail_payload())
    assert panel_quota.set_panel_enabled(False) == 1
    assert panel_quota.has_pending(571494255, "10001", 30) is False
    panel_quota.set_panel_enabled(None)


def test_enable_keeps_pending():
    event = make_event()
    panel_quota.register_pending(event, feature="detail", payload=_detail_payload())
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


async def test_ensure_panel_available_gate(monkeypatch):
    from nonebot_plugin_miao.commands import common

    class FakeMatcher:
        async def finish(self, message: str = ""):
            raise RuntimeError(str(message))

    monkeypatch.setattr(common, "panel_enabled", lambda: True)
    monkeypatch.setattr(common, "panel_time_allowed", lambda: False)
    monkeypatch.setattr(common, "panel_closed_message", lambda: "面板已关闭")
    with pytest.raises(RuntimeError, match="面板已关闭"):
        await common.ensure_panel_available(FakeMatcher(), object())

    monkeypatch.setattr(common, "panel_time_allowed", lambda: True)
    assert await common.ensure_panel_available(FakeMatcher(), object()) is None


async def test_ensure_panel_available_allows_wheel_draws(monkeypatch):
    from nonebot_plugin_miao.commands import common

    class FakeMatcher:
        async def finish(self, message: str = ""):
            raise RuntimeError(str(message))

    monkeypatch.setattr(common, "panel_enabled", lambda: True)
    monkeypatch.setattr(common, "panel_time_allowed", lambda: False)
    monkeypatch.setattr(common, "panel_closed_message", lambda: "面板已关闭")

    seen: list[str | None] = []

    async def has_draws(event, feature="detail"):
        seen.append(feature)
        return True

    event = make_event()
    monkeypatch.setattr(common, "has_panel_draws", has_draws)
    assert await common.ensure_panel_available(FakeMatcher(), event, feature="detail") is None
    assert await common.ensure_panel_available(FakeMatcher(), event, feature="rank") is None
    assert seen == ["detail", "rank"]
    # 不消耗面板次数的指令仍然受时段限制
    with pytest.raises(RuntimeError, match="面板已关闭"):
        await common.ensure_panel_available(FakeMatcher(), event)

    async def no_draws(event, feature="detail"):
        return False

    monkeypatch.setattr(common, "has_panel_draws", no_draws)
    monkeypatch.setattr(common, "panel_offhour_access", lambda event: False)
    with pytest.raises(RuntimeError, match="面板已关闭"):
        await common.ensure_panel_available(FakeMatcher(), event, feature="detail")

    # 非开放时段可付费查看时放行，交给授权阶段扣好感度
    monkeypatch.setattr(common, "panel_offhour_access", lambda event: True)
    assert await common.ensure_panel_available(FakeMatcher(), event, feature="detail") is None
