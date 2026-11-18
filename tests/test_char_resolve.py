"""角色名容错：别名、归一化与常见错字。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonebot_plugin_miao.core import meta
from nonebot_plugin_miao.core.player import Player
from nonebot_plugin_miao.datasource.enka import parse_enka

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("query", "game", "expected"),
    [
        ("优菈", "gs", "优菈"),
        ("优拉", "gs", "优菈"),
        ("优菈的", "gs", "优菈"),
        ("优 菈", "gs", "优菈"),
        ("纳西达", "gs", "纳西妲"),
        ("芙琳娜", "gs", "芙宁娜"),
        ("钟璃", "gs", "钟离"),
        ("可利", "gs", "可莉"),
        ("胡陶", "gs", "胡桃"),
        ("神里凌华", "gs", "神里绫华"),
        ("雷神", "gs", "雷电将军"),
        ("心海", "gs", "珊瑚宫心海"),
        ("丹恒饮月", "sr", "丹恒•饮月"),
        ("镜琉", "sr", "镜流"),
        ("流莹", "sr", "流萤"),
        ("卡夫卡", "sr", "卡芙卡"),
        ("不存在的角色", "gs", None),
        ("不存在的铁道角色", "sr", None),
        # 同音错字可以区分绫华/绫人；只写共同前缀时不猜
        ("神里绫花", "gs", "神里绫华"),
        ("神里绫", "gs", None),
        ("", "gs", None),
    ],
)
def test_resolve_character(query, game, expected):
    char = meta.resolve_character(query, game)
    assert (char.name if char else None) == expected


def test_resolve_character_keeps_numeric_id_exact():
    assert meta.resolve_character("10000052", "gs").name == "雷电将军"
    assert meta.resolve_character("99999999", "gs") is None


def test_suggest_characters():
    assert meta.suggest_characters("优拉", "gs")[0] == "优菈"
    assert meta.suggest_characters("不存在的角色", "gs") == []


def test_command_words_are_not_treated_as_typos():
    # 「抽卡」和「米卡」只差一个字，但不能把指令词纠成角色
    assert meta.resolve_character("抽卡", "gs") is None
    assert meta.suggest_characters("抽卡", "gs") == []
    assert meta.resolve_character("更新", "gs") is None
    assert meta.resolve_character("圣遗物", "gs") is None


def test_player_get_avatar_typo():
    raw = json.loads((FIXTURES / "enka_800055548.json").read_text(encoding="utf-8"))
    player = Player("800055548", "gs", parse_enka(raw, "800055548"))
    assert player.get_avatar("优拉")["name"] == "优菈"
    assert player.get_avatar("不存在的角色") is None
