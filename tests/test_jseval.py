"""JS 引擎双后端（quickjs / pythonmonkey）路径测试。"""
import json
from pathlib import Path

import pytest

from nonebot_plugin_miao.core import jseval
from nonebot_plugin_miao.datasource.enka import parse_enka


def test_backend_available():
    assert jseval.backend() in ("quickjs", "pythonmonkey")
    assert json.loads(jseval.eval_js("JSON.stringify({a: 1 + 1})")) == {"a": 2}


def test_pythonmonkey_path_eval_esm(monkeypatch):
    pytest.importorskip("pythonmonkey")
    monkeypatch.setattr(jseval, "_BACKEND", "pythonmonkey")
    from nonebot_plugin_miao.core import meta

    extra = meta.artifact_extra("gs")
    assert "attrMap" in extra and "hp" in extra["attrMap"]
    pools = meta.pool_data("gs")
    assert len(pools) > 50


def test_pythonmonkey_path_calc_dmg(monkeypatch):
    pytest.importorskip("pythonmonkey")
    monkeypatch.setattr(jseval, "_BACKEND", "pythonmonkey")
    from nonebot_plugin_miao.dmg import calc_dmg

    raw = json.loads((Path(__file__).parent / "fixtures" / "enka_800055548.json").read_text(encoding="utf-8"))
    avatars = parse_enka(raw, "800055548")["avatars"]
    result = calc_dmg(avatars["10000016"], "gs")
    assert result["character"] == "迪卢克"
    assert len(result["dmgData"]) >= 3
    assert result["selected"]["avg"] > 0
