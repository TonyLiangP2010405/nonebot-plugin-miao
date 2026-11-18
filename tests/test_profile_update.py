"""更新面板图片回执：接口只返回部分角色时仍展示本地全部角色。"""
from __future__ import annotations

import json
from pathlib import Path

from nonebot_plugin_miao.core.player import Player
from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.render.profile_update import update_items

FIXTURE = Path(__file__).parent / "fixtures" / "enka_800055548.json"


def test_update_items_keeps_existing_characters():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    player = Player("800055548", "gs", parse_enka(raw, "800055548"))
    assert len(player.avatars) == 8

    items = update_items(player, "gs", ["优菈", "钟离"])
    assert len(items) == 8
    assert sum(1 for item in items if item[3]) == 2
    assert {item[0] for item in items[:2]} == {"优菈", "钟离"}
    assert all(item[0] for item in items)


def test_update_items_without_new_characters():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    player = Player("800055548", "gs", parse_enka(raw, "800055548"))
    items = update_items(player, "gs", [])
    assert len(items) == 8
    assert all(not item[3] for item in items)
