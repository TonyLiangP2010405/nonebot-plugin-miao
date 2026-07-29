"""QuickJS 伤害规则执行测试。"""
import json
from pathlib import Path

import pytest

from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.dmg import DamageError, calc_dmg


@pytest.fixture(scope="module")
def avatars() -> dict:
    raw = json.loads((Path(__file__).parent / "fixtures" / "enka_800055548.json").read_text(encoding="utf-8"))
    return parse_enka(raw, "800055548")["avatars"]


def test_calc_dmg_runs_upstream_rule(avatars):
    result = calc_dmg(avatars["10000016"], "gs")
    assert result["character"] == "迪卢克"
    assert len(result["dmgData"]) >= 3
    assert result["selected"]["avg"] > 0
    assert any("蒸发" in row["title"] for row in result["dmgData"])


def test_calc_dmg_rule_callback_and_explicit_index(avatars):
    result = calc_dmg(avatars["10000051"], "gs", 4)
    assert result["selected"]["title"].startswith("光降之剑")
    assert result["selected"]["dmg"] > result["selected"]["avg"] > 0


def test_calc_dmg_rejects_out_of_range_index(avatars):
    with pytest.raises(DamageError, match="序号输入错误"):
        calc_dmg(avatars["10000016"], "gs", 99)
