"""十连模拟抽卡单元测试：注入固定 rng + monkeypatch 时间/数据目录"""
from datetime import datetime

import pytest

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.gacha import simulate

GROUP_SCOPE = "12345:67890"
PRIVATE_SCOPE = "private:67890"


class FakeRng:
    """按 (min,max) 分派的固定随机源

    - max=10000：五/四星判定档，roll_10000=10000 表示"只有概率满 10000 才中"
    - max=100：UP/对半判定档（roll_100=1 必中 UP，roll_100=100 必歪）
    - 其余：lodash.sample 的取元素档，默认取第一个元素
    """

    def __init__(self, roll_10000: int = 10000, roll_100: int = 1, sample: int = 1):
        self.roll_10000 = roll_10000
        self.roll_100 = roll_100
        self.sample = sample

    def __call__(self, min_: int, max_: int) -> int:
        if max_ == 10000:
            return self.roll_10000
        if max_ == 100:
            return self.roll_100
        return min(self.sample, max_)


def _ts(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s).timestamp()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录重定向到临时目录"""
    d = tmp_path / "data"
    monkeypatch.setattr(store, "_data_dir", lambda: d)
    return d


def _set_counter(scope, gacha_type, **kwargs):
    """直接改用户某个池子的保底计数"""
    user = store.read_sim_state(scope)
    user[gacha_type].update(kwargs)
    store.write_sim_state(scope, user)


# ---------------- 五星保底 ----------------


def test_five_pity_hit_at_90_misses(data_dir):
    """连续 90 次不中（num5=90）后下一发必中五星"""
    # 先抽一发建立用户状态
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    assert res["code"] == "ok"
    assert all(v["star"] < 5 for v in res["list"])  # roll 恒 10000，五星不可能中（第10发会吃四星保底）

    _set_counter(GROUP_SCOPE, "role", num5=90)
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    fives = [v for v in res["list"] if v["star"] == 5]
    assert len(fives) == 1
    assert fives[0]["index"] == 1  # 第一发即中
    assert fives[0]["num"] == 91  # 第 91 抽
    # 命中后保底清零，随后 9 发未中 → num5=9
    assert store.read_sim_state(GROUP_SCOPE)["role"]["num5"] == 9


def test_four_pity_hit_at_10(data_dir):
    """四星 10 连必中（num4=9 时下一发必中四星）"""
    simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    _set_counter(GROUP_SCOPE, "role", num4=9)
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    fours = [v for v in res["list"] if v["star"] == 4]
    assert len(fours) == 1
    assert fours[0]["index"] == 1
    # roll_100=1 必中 UP，sample=1 取 up4 第一个
    assert fours[0]["name"] == simulate.get_pool("role")["up4"][0]


# ---------------- 大保底 ----------------


def test_big_pity_after_wai(data_dir):
    """歪一次后下次五星必为 UP（isBigUP）"""
    simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    _set_counter(GROUP_SCOPE, "role", num5=90)
    # roll_100=100 > wai(50) → 歪
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng(roll_100=100))
    five = [v for v in res["list"] if v["star"] == 5][0]
    assert five["name"] in simulate.get_pool("role")["five"]
    assert not five["isBigUP"]
    assert store.read_sim_state(GROUP_SCOPE)["role"]["isUp5"] == 1

    # 下次五星 tmpUp=101，roll_100=1 必中 UP
    _set_counter(GROUP_SCOPE, "role", num5=90)
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng(roll_100=1))
    five = [v for v in res["list"] if v["star"] == 5][0]
    assert five["name"] in simulate.get_pool("role")["up5"]
    assert five["isBigUP"]
    assert store.read_sim_state(GROUP_SCOPE)["role"]["isUp5"] == 0
    assert "大保底" in res["info"]


# ---------------- 定轨 ----------------


def test_bing_weapon_at_life_num_2(data_dir):
    """命定值到 2 后下次五星必为定轨武器（isBing），命定值清零"""
    simulate.do_gacha(GROUP_SCOPE, "weapon", daily_limit=100, rng=FakeRng())
    _set_counter(GROUP_SCOPE, "weapon", num5=80, lifeNum=2, type=1)
    res = simulate.do_gacha(GROUP_SCOPE, "weapon", daily_limit=100, rng=FakeRng(roll_100=100))
    five = [v for v in res["list"] if v["star"] == 5][0]
    assert five["isBing"]
    assert five["name"] == simulate.get_pool("weapon")["up5"][0]  # type=1 → 武器1
    assert store.read_sim_state(GROUP_SCOPE)["weapon"]["lifeNum"] == 0
    assert "定轨" in res["info"]
    assert res["isWeapon"]


def test_toggle_bing_cycle(data_dir):
    """定轨 type 1→2→0 循环，命定值清零，文案含 [√] 列表"""
    now_pool = simulate.get_now_pool()
    # 初始 type=1，切换后 type=2 → 勾选武器2
    msg = simulate.toggle_bing(GROUP_SCOPE)
    assert msg.startswith("定轨成功")
    assert f"[√] {now_pool['weapon5'][1]}" in msg
    assert f"[  ] {now_pool['weapon5'][0]}" in msg
    user = store.read_sim_state(GROUP_SCOPE)
    assert user["weapon"]["type"] == 2

    # type=2 → 0 取消
    msg = simulate.toggle_bing(GROUP_SCOPE)
    assert msg == "\n定轨已取消"
    user = store.read_sim_state(GROUP_SCOPE)
    assert user["weapon"]["type"] == 0

    # type=0 → 1 → 勾选武器1
    msg = simulate.toggle_bing(GROUP_SCOPE)
    assert msg.startswith("定轨成功")
    assert f"[√] {now_pool['weapon5'][0]}" in msg
    assert store.read_sim_state(GROUP_SCOPE)["weapon"]["type"] == 1


# ---------------- 每日限制 ----------------


def test_daily_limit(data_dir):
    """超过 daily_limit 次后返回 limit 文案，master 不限"""
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=1, rng=FakeRng())
    assert res["code"] == "ok"

    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=1, rng=FakeRng())
    assert res["code"] == "limit"
    assert "今日" in res["msg"]
    assert "10抽无五星" in res["msg"]  # 全歪三星时：今日已抽，累计10抽无五星

    # master 不限
    res = simulate.do_gacha(GROUP_SCOPE, "role", is_master=True, daily_limit=1, rng=FakeRng())
    assert res["code"] == "ok"

    # 私聊 scope 独立计数
    res = simulate.do_gacha(PRIVATE_SCOPE, "role", daily_limit=1, rng=FakeRng())
    assert res["code"] == "ok"


# ---------------- 每日 4 点重置 ----------------


def test_reset_at_4am(data_dir, monkeypatch):
    """次日 4 点前不重置，4 点后 today 重置"""
    monkeypatch.setattr(simulate, "_now", lambda: _ts(2025, 3, 10, 10))
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=1, rng=FakeRng())
    assert res["code"] == "ok"
    assert store.read_sim_state(GROUP_SCOPE)["today"]["num"] == 10

    # 次日 02:00（4 点前）：仍未重置，超限
    monkeypatch.setattr(simulate, "_now", lambda: _ts(2025, 3, 11, 2))
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=1, rng=FakeRng())
    assert res["code"] == "limit"

    # 次日 05:00（4 点后）：today 重置，可再抽
    monkeypatch.setattr(simulate, "_now", lambda: _ts(2025, 3, 11, 5))
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=1, rng=FakeRng())
    assert res["code"] == "ok"
    assert store.read_sim_state(GROUP_SCOPE)["today"]["num"] == 10  # 重置后重新计 10


# ---------------- 卡池选择 ----------------


def test_pool_selection(data_dir, monkeypatch):
    """时间落在某期池区间内时 up5 正确；十连2 用 up5_2"""
    # 2025-03-10 落在 endTime=2025-03-25 14:59:59 的池（芙宁娜/莱欧斯利）区间内
    monkeypatch.setattr(simulate, "_now", lambda: _ts(2025, 3, 10, 12))

    pool = simulate.get_pool("role")
    assert pool["up5"] == ["芙宁娜"]
    assert "芙宁娜" not in pool["five"]

    pool2 = simulate.get_pool("role2")
    assert pool2["up5"] == ["莱欧斯利"]
    # role 与 role2 共用 "role" 保底计数
    assert simulate._gacha_type("role2") == "role"

    weapon = simulate.get_pool("weapon")
    assert weapon["up5"] == ["静水流涌之辉", "金流监督"]
    assert weapon["up4"] == ["西风剑", "祭礼大剑", "匣里灭辰", "祭礼残章", "弓藏"]
    # 常驻四星武器 = def.weapon4 差集 up4
    assert set(weapon["weapon4"]) == set(simulate.meta.gacha_sim_config()["gacha"]["weapon4"]) - set(weapon["up4"])

    permanent = simulate.get_pool("permanent")
    gacha_def = simulate.meta.gacha_sim_config()["gacha"]
    assert permanent["up5"] == [] and permanent["up4"] == []
    assert permanent["five"] == gacha_def["role5"]
    assert permanent["fiveW"] == gacha_def["weapon5"]


def test_pool_fallback_when_expired(data_dir, monkeypatch):
    """所有池都过期时用倒序最后一条（照 JS poolArr.pop()）"""
    monkeypatch.setattr(simulate, "_now", lambda: _ts(2099, 1, 1))
    pools = simulate.meta.gacha_sim_config()["pool"]
    assert simulate.get_now_pool() == pools[0]  # pool.json 第一条是最新池


# ---------------- 排序 ----------------


def test_result_sorted_by_star(data_dir):
    """结果按星级降序、index 升序排序"""
    simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    # 第 1 发中五星（num5=90），第 2 发中四星（num4 因五星 +1 后为 9 → 必中）
    _set_counter(GROUP_SCOPE, "role", num5=90, num4=8)
    res = simulate.do_gacha(GROUP_SCOPE, "role", daily_limit=100, rng=FakeRng())
    stars = [v["star"] for v in res["list"]]
    assert stars == sorted(stars, reverse=True)
    assert stars[0] == 5 and stars[1] == 4
    assert stars.count(3) == 8
    # 同星级按 index 升序
    indexes = [v["index"] for v in res["list"] if v["star"] == 3]
    assert indexes == sorted(indexes)
