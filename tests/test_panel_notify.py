"""面板开放 / 关闭群提醒的单元测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nonebot_plugin_miao.core import panel_notify, store

_TZ = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)
    return tmp_path


def make_config(**overrides):
    data = {
        "miao_panel_open_hour": 8,
        "miao_panel_close_hour": 17,
        "miao_panel_free_per_hour": 1,
        "miao_panel_user_hourly_limit": 2,
        "miao_panel_affection_cost": 10,
        "miao_panel_offhour_affection_cost": 50,
        "miao_panel_notify_enabled": True,
        "miao_panel_notify_repeat": 3,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_panel_notify_text_mentions_offhour_price():
    open_text = panel_notify.panel_notify_text("open", make_config())
    assert "面板功能已开启" in open_text
    assert "08:00–17:00" in open_text
    assert "50 点好感度" in open_text
    assert "回复「确认」" in open_text

    close_text = panel_notify.panel_notify_text("close", make_config())
    assert "面板功能已关闭" in close_text
    assert "50 点好感度" in close_text
    assert "/面板次数" in close_text

    disabled = panel_notify.panel_notify_text("close", make_config(miao_panel_offhour_affection_cost=0))
    assert "暂不可查看" in disabled


async def test_notify_sends_three_times_per_group(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.sent: list[tuple[int, str]] = []

        async def send_group_msg(self, group_id, message):
            self.sent.append((group_id, message))

    async def no_sleep(_seconds):
        return None

    bot = FakeBot()
    monkeypatch.setattr(panel_notify, "_pick_bot", lambda: bot)
    monkeypatch.setattr(panel_notify.store, "panel_groups", lambda: ["100", "200"])
    monkeypatch.setattr(panel_notify.asyncio, "sleep", no_sleep)

    sent = await panel_notify.notify_panel_window("open", make_config())
    assert sent == 6
    assert [gid for gid, _ in bot.sent] == [100, 100, 100, 200, 200, 200]
    assert all("50 点好感度" in text for _, text in bot.sent)


async def test_notify_disabled(monkeypatch):
    class FakeBot:
        async def send_group_msg(self, group_id, message):
            raise AssertionError("不应发送")

    monkeypatch.setattr(panel_notify, "_pick_bot", lambda: FakeBot())
    monkeypatch.setattr(panel_notify.store, "panel_groups", lambda: ["100"])
    assert (
        await panel_notify.notify_panel_window(
            "open", make_config(miao_panel_notify_enabled=False)
        )
        == 0
    )


async def test_notify_skips_without_groups(monkeypatch):
    monkeypatch.setattr(panel_notify.store, "panel_groups", lambda: [])
    assert await panel_notify.notify_panel_window("close", make_config()) == 0


async def test_panel_window_tick(monkeypatch):
    calls: list[str] = []

    async def fake_notify(kind, config=None, **kwargs):
        calls.append(kind)
        return 3

    monkeypatch.setattr(panel_notify, "_plugin_config", lambda: make_config())
    monkeypatch.setattr(panel_notify, "notify_panel_window", fake_notify)

    assert await panel_notify.panel_window_tick(datetime(2026, 1, 1, 8, 0, tzinfo=_TZ)) == 3
    assert await panel_notify.panel_window_tick(datetime(2026, 1, 1, 17, 0, tzinfo=_TZ)) == 3
    assert await panel_notify.panel_window_tick(datetime(2026, 1, 1, 12, 0, tzinfo=_TZ)) == 0
    assert calls == ["open", "close"]

    monkeypatch.setattr(
        panel_notify, "_plugin_config", lambda: make_config(miao_panel_open_hour=0, miao_panel_close_hour=0)
    )
    assert await panel_notify.panel_window_tick(datetime(2026, 1, 1, 8, 0, tzinfo=_TZ)) == 0


def test_panel_group_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)
    assert store.panel_groups() == []
    assert store.mark_panel_group(123456) is True
    assert store.mark_panel_group("123456") is False
    assert store.mark_panel_group("not-a-group") is False
    assert store.mark_panel_group("") is False
    assert store.panel_groups() == ["123456"]
