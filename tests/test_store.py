"""store 数据存储层单元测试：monkeypatch _data_dir 到 tmp_path，直接测纯函数"""
import json

import pytest

from nonebot_plugin_miao.core import store


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录重定向到临时目录"""
    d = tmp_path / "data"
    monkeypatch.setattr(store, "_data_dir", lambda: d)
    return d


# ---------------- UID 绑定 ----------------


def test_bind_and_get_uid(data_dir):
    assert store.bind_uid(12345, "gs", "100000001") == "100000001"
    assert store.get_uid(12345, "gs") == "100000001"
    assert store.get_uid("12345", "gs") == "100000001"  # int/str 等价
    assert store.get_uid(12345, "sr") is None  # 分游戏隔离
    # 持久化到了 bind.json
    binds = json.loads((data_dir / "bind.json").read_text(encoding="utf-8"))
    assert binds["gs"]["12345"] == "100000001"


def test_bind_uid_validation(data_dir):
    for bad in ["123", "abcdefghij", "0123456789", "10000000"]:  # 位数不足/非数字/0开头
        with pytest.raises(ValueError):
            store.bind_uid(1, "gs", bad)
    # 9-10 位合法，18 开头 11 位也合法
    assert store.bind_uid(1, "gs", "100000000") == "100000000"
    assert store.bind_uid(1, "sr", "1800000000") == "1800000000"


def test_bind_invalid_game(data_dir):
    with pytest.raises(ValueError):
        store.bind_uid(1, "xx", "100000001")
    with pytest.raises(ValueError):
        store.get_uid(1, "xx")


def test_del_bind(data_dir):
    store.bind_uid(1, "gs", "100000001")
    store.bind_uid(1, "sr", "800000001")
    # 只解绑 gs
    assert store.del_bind(1, "gs") is True
    assert store.get_uid(1, "gs") is None
    assert store.get_uid(1, "sr") == "800000001"
    # game=None 解绑所有
    assert store.del_bind(1) is True
    assert store.get_uid(1, "sr") is None
    # 重复解绑返回 False
    assert store.del_bind(1) is False


# ---------------- cookie ----------------


def test_cookie(data_dir):
    assert store.get_cookie(1) is None
    store.set_cookie(1, "ltoken=v2_abc; ltuid=123")
    assert store.get_cookie(1) == "ltoken=v2_abc; ltuid=123"
    assert (data_dir / "cookies.json").is_file()
    assert store.del_cookie(1) is True
    assert store.get_cookie(1) is None
    assert store.del_cookie(1) is False


# ---------------- 冷却 ----------------


def test_cd(data_dir):
    assert store.check_cd("gacha:1", 60) == 0  # 未设置时可用
    store.set_cd("gacha:1", 60)
    remaining = store.check_cd("gacha:1", 60)
    assert 0 < remaining <= 60
    # 其他 key 不受影响
    assert store.check_cd("gacha:2", 60) == 0


def test_cd_expired(data_dir):
    # 直接写入一个已过期的到期时间戳
    store.save_json(data_dir / "cd.json", {"old": 1})
    assert store.check_cd("old", 60) == 0


# ---------------- 抽卡记录 ----------------


def test_gacha_log_path_and_rw(data_dir):
    path = store.gacha_log_path(12345, "100000001", 301, "gs")
    assert path == data_dir / "gacha" / "gs" / "12345" / "100000001" / "301.json"
    assert path.parent.is_dir()  # 自动 mkdir
    # 读取不存在的记录返回空列表
    assert store.read_gacha_log(12345, "100000001", 301, "gs") == []
    logs = [{"id": "1", "name": "刻晴", "rank_type": "5", "time": "2024-01-01 00:00:00"}]
    store.write_gacha_log(12345, "100000001", 301, "gs", logs)
    assert store.read_gacha_log(12345, "100000001", 301, "gs") == logs
    # sr 路径隔离
    sr_path = store.gacha_log_path(12345, "800000001", 1, "sr")
    assert sr_path == data_dir / "gacha" / "sr" / "12345" / "800000001" / "1.json"


# ---------------- 面板数据 ----------------


def test_player_rw(data_dir):
    path = store.player_path("gs", "100000001")
    assert path == data_dir / "player" / "gs" / "100000001.json"
    assert store.read_player("gs", "100000001") == {}
    payload = {"uid": "100000001", "avatar": [{"name": "刻晴", "level": 90}]}
    store.write_player("gs", "100000001", payload)
    assert store.read_player("gs", "100000001") == payload


# ---------------- 模拟抽卡状态 ----------------


def test_sim_state_rw(data_dir):
    assert store.read_sim_state("group_123:456") == {}
    state = {"count": 3, "date": "2024-01-01"}
    store.write_sim_state("group_123:456", state)
    assert store.sim_state_path("group_123:456") == data_dir / "sim" / "group_123:456.json"
    assert store.read_sim_state("group_123:456") == state
    # private 作用域互不影响
    assert store.read_sim_state("private:456") == {}


# ---------------- 通用 load/save ----------------


def test_load_save_json(tmp_path):
    p = tmp_path / "sub" / "x.json"
    assert store.load_json(p, {"a": 1}) == {"a": 1}  # 不存在返回 default
    assert store.load_json(p) is None
    store.save_json(p, {"a": 1, "b": [1, 2, 3]})  # 自动创建父目录
    assert store.load_json(p) == {"a": 1, "b": [1, 2, 3]}
    # 损坏文件返回 default
    p.write_text("{bad json", encoding="utf-8")
    assert store.load_json(p, {}) == {}
    # 原子写：不残留临时文件
    store.save_json(p, {"ok": True})
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]
