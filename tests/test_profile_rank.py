"""群面板排名：成员收集、评分/伤害排序、限额与排名/更新卡片渲染。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import skia

from nonebot_plugin_miao.core import profile_rank
from nonebot_plugin_miao.core.player import Player
from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.render import base
from nonebot_plugin_miao.render.profile_rank import render_profile_rank
from nonebot_plugin_miao.render.profile_update import render_profile_update

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBot:
    def __init__(self, fail: bool = False):
        self.fail = fail

    async def get_group_member_list(self, group_id):
        if self.fail:
            raise RuntimeError("api unsupported")
        return [
            {"user_id": 10001, "nickname": "旅行者", "card": "群主"},
            {"user_id": 10002, "nickname": "路人"},
            {"user_id": 10003, "nickname": "没绑定的人"},
        ]


@pytest.fixture
def player() -> Player:
    raw = json.loads((FIXTURES / "enka_800055548.json").read_text(encoding="utf-8"))
    data = parse_enka(raw, "800055548")
    return Player("800055548", "gs", data)


@pytest.fixture
def bound(monkeypatch, player):
    def fake_load(cls, uid, game="gs"):
        return player

    monkeypatch.setattr(profile_rank.Player, "load", classmethod(fake_load))
    monkeypatch.setattr(
        profile_rank.store,
        "get_uid",
        lambda qq, game: {"10001": "800055548", "10002": "800055548"}.get(str(qq)),
    )
    return player


async def test_collect_character_rank(bound):
    entries = await profile_rank.collect_rank(FakeBot(), 571494255, "gs", "优菈")
    assert len(entries) == 2
    assert all(entry.char_name == "优菈" for entry in entries)
    assert all(entry.score > 0 and entry.mark > 0 for entry in entries)
    assert [entry.nickname for entry in entries] == ["群主", "路人"]
    assert entries[0].uid == "800055548"


async def test_collect_damage_rank(bound):
    entries = await profile_rank.collect_rank(
        FakeBot(), 571494255, "gs", "优菈", mode=profile_rank.MODE_DMG
    )
    assert len(entries) == 2
    assert all(entry.dmg_avg and entry.dmg_avg > 0 for entry in entries)
    assert entries[0].score == pytest.approx(entries[0].dmg_avg)


async def test_collect_all_uses_best_character(bound):
    entries = await profile_rank.collect_rank(FakeBot(), 571494255, "gs")
    assert len(entries) == 2
    assert all(entry.char_name for entry in entries)
    # 同一个人只出现一次，取评分最高的角色
    assert len({entry.qq for entry in entries}) == 2


async def test_collect_rank_limit(bound):
    entries = await profile_rank.collect_rank(FakeBot(), 571494255, "gs", "优菈", limit=1)
    assert len(entries) == 1


async def test_group_member_api_fallback(monkeypatch, bound):
    monkeypatch.setattr(
        profile_rank.store,
        "all_binds",
        lambda game: {"10001": "800055548", "10002": "800055548"},
    )
    entries = await profile_rank.collect_rank(FakeBot(fail=True), 571494255, "gs", "优菈")
    assert len(entries) == 2


def _check_png(data: bytes) -> None:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    image = skia.Image.MakeFromEncoded(data)
    assert image is not None and image.width() > 0 and image.height() > 0


@pytest.fixture
def no_images(monkeypatch):
    async def _none(rel):
        return None

    monkeypatch.setattr(base, "fetch_image", _none)


async def test_render_rank_card(bound, no_images):
    entries = await profile_rank.collect_rank(FakeBot(), 571494255, "gs", "优菈")
    png = await render_profile_rank(
        entries,
        "gs",
        char_name="优菈",
        mode=profile_rank.MODE_MARK,
        group_label="群 571494255",
        member_count=len(entries),
    )
    _check_png(png)


async def test_render_rank_card_empty(no_images):
    png = await render_profile_rank([], "gs", group_label="群 571494255")
    _check_png(png)


async def test_render_update_card(player, no_images):
    png = await render_profile_update(
        player, "gs", ["优菈", "钟离"], "800055548", "Enka"
    )
    _check_png(png)


async def test_render_update_card_no_new(player, no_images):
    png = await render_profile_update(player, "gs", [], "800055548", "Enka")
    _check_png(png)
