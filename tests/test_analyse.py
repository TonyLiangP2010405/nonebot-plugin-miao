"""P3 抽卡分析测试：analyse 单池分析 + stat 按版本统计

数据用 tools/fixtures 下的手工构造记录（与 tools/verify_gacha.mjs 交叉验证用例同源），
store._data_dir monkeypatch 到 fixture 目录。断言值均经过原版 GachaData.js 基准输出核对
（见 tools/js_out.json），覆盖：有歪有不歪、跨版本、无五星占位、集录池、星铁各池。
"""
from datetime import datetime
from pathlib import Path

import pytest

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.gacha import analyse as ga

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tools" / "fixtures"
GS_UID = "100000001"
SR_UID = "800000001"
TODAY = datetime.now().strftime("%m-%d")


@pytest.fixture
def data_dir(monkeypatch):
    """把插件数据目录重定向到 fixture 目录（只读）"""
    monkeypatch.setattr(store, "_data_dir", lambda: FIXTURE_DIR)
    return FIXTURE_DIR


# ---------------- pool_type_of / 高层封装 ----------------


def test_pool_type_of():
    assert ga.pool_type_of("up", "gs") == 301
    assert ga.pool_type_of("角色", "gs") == 301
    assert ga.pool_type_of("抽卡", "gs") == 301
    assert ga.pool_type_of("抽奖", "gs") == 301
    assert ga.pool_type_of("常驻", "gs") == 200
    assert ga.pool_type_of("武器", "gs") == 302
    assert ga.pool_type_of("集录", "gs") == 500
    assert ga.pool_type_of("角色", "sr") == 11
    assert ga.pool_type_of("常驻", "sr") == 1
    assert ga.pool_type_of("武器", "sr") == 12
    assert ga.pool_type_of("光锥", "sr") == 12
    assert ga.pool_type_of("不存在", "gs") is None
    # 星铁没有集录池
    assert ga.pool_type_of("集录", "sr") is None


def test_analyse_pool_keyword(data_dir):
    assert ga.analyse_pool(1, GS_UID, "角色", "gs") == ga.analyse(1, GS_UID, 301, "gs")
    assert ga.analyse_pool(1, GS_UID, "集录", "gs") == ga.analyse(1, GS_UID, 500, "gs")
    assert ga.analyse_pool(1, GS_UID, "不存在", "gs") is None


def test_stat_pool_keyword(data_dir):
    assert ga.stat_pool(1, GS_UID, "全部", "gs") == ga.stat(1, GS_UID, "all", "gs")
    assert ga.stat_pool(2, SR_UID, "光锥", "sr") == ga.stat(2, SR_UID, "weapon", "sr")
    assert ga.stat_pool(1, GS_UID, "不存在", "gs") is None


# ---------------- analyse：原神角色池（跨版本、有歪有不歪、垫抽占位） ----------------


def test_analyse_gs_up(data_dir):
    result = ga.analyse(1, GS_UID, 301, "gs")
    stat = result["stat"]
    assert stat["allNum"] == 10
    assert stat["fiveNum"] == 3
    assert stat["fourNum"] == 3
    assert stat["noFiveNum"] == 2  # 最后 2 抽在垫
    assert stat["noFourNum"] == 1
    assert stat["wai"] == 1  # 莫娜
    assert stat["fiveAvg"] == "2.67"  # (10-2)/3
    assert stat["fourAvg"] == "3.00"  # (10-1)/3
    assert stat["isvalidNum"] == "4.00"  # (10-2)/(3-1)
    assert stat["weaponNum"] == 0  # JS 版恒为 0
    assert stat["weaponFourNum"] == 2
    assert stat["upYs"] == "640"  # 4.00*160
    assert result["noWaiRate"] == "66.7"  # (3-1)/3

    # fiveLog：头部是 "已抽" 占位（count=垫抽数，date=今天），之后按时间倒序
    five_log = result["fiveLog"]
    assert [(e["id"], e["isUp"], e["count"]) for e in five_log] == [
        (888, True, 2),
        (10000089, True, 3),  # 芙宁娜 UP
        (10000087, True, 2),  # 那维莱特 UP
        (10000041, False, 3),  # 莫娜歪
    ]
    assert five_log[0]["date"] == TODAY
    assert [e["date"] for e in five_log[1:]] == ["11-10", "10-02", "09-30"]

    # 占位物品已注入 items
    assert result["items"][888] == {"name": "已抽", "star": 5, "abbr": "已抽", "img": "gacha/imgs/no-avatar.webp"}

    # 四星最多：三个四星各 1 张平票，按 id 数值升序取第一个（西风剑 11401）
    assert result["maxFour"]["name"] == "西风剑"
    assert result["maxFour"]["count"] == 1


def test_analyse_gs_weapon(data_dir):
    result = ga.analyse(1, GS_UID, 302, "gs")
    stat = result["stat"]
    assert stat["allNum"] == 3
    assert stat["fiveNum"] == 2
    assert stat["wai"] == 1  # 四风原典
    assert stat["noFiveNum"] == 0  # 最新一条就是五星，无垫抽无占位
    assert stat["fiveAvg"] == "1.50"
    assert stat["fourAvg"] == 0  # 无四星时是数字 0（照抄 JS）
    # 最近一个五星是歪的：isvalidNum 剔除这一段 (3-0-1)/(2-1)
    assert stat["isvalidNum"] == "2.00"
    assert stat["upYs"] == "320"
    assert result["noWaiRate"] == "50.0"
    assert [(e["id"], e["isUp"], e["count"]) for e in result["fiveLog"]] == [
        (14502, False, 1),  # 四风原典
        ("14514", True, 2),  # 万世流涌大典（meta 里 id 是字符串，与 JS 一致）
    ]
    assert 888 not in result["items"]


def test_analyse_gs_normal(data_dir):
    result = ga.analyse(1, GS_UID, 200, "gs")
    stat = result["stat"]
    assert stat["fiveNum"] == 1
    assert stat["wai"] == 1  # 迪卢克不在任何 UP 池 char5
    # fiveNum == wai 时 isvalidNum 为 0，upYs 为 "0"
    assert stat["isvalidNum"] == 0
    assert stat["upYs"] == "0"
    assert result["noWaiRate"] == "0.0"
    assert result["fiveLog"][0]["id"] == 888
    assert result["fiveLog"][0]["count"] == 1


def test_analyse_gs_mix(data_dir):
    """集录池：isUp 判定走 mixPoolDetail（优先于普通 poolDetail）"""
    result = ga.analyse(1, GS_UID, 500, "gs")
    stat = result["stat"]
    assert stat["allNum"] == 3
    assert stat["fiveNum"] == 2
    assert stat["wai"] == 0
    assert result["noWaiRate"] == "100.0"
    assert [(e["id"], e["isUp"]) for e in result["fiveLog"]] == [
        (12503, True),  # 松籁响起之时（集录 weapon5）
        (10000051, True),  # 优菈（集录 char5）
    ]


# ---------------- analyse：星铁 ----------------


def test_analyse_sr_char(data_dir):
    result = ga.analyse(2, SR_UID, 11, "sr")
    stat = result["stat"]
    assert stat["allNum"] == 4
    assert stat["fiveNum"] == 2
    assert stat["wai"] == 1  # 布洛妮娅
    assert stat["noFiveNum"] == 1
    assert stat["fiveAvg"] == "1.50"
    assert stat["isvalidNum"] == "3.00"  # (4-1)/(2-1)
    assert stat["upYs"] == "480"
    assert result["noWaiRate"] == "50.0"
    assert [(e["id"], e["isUp"], e["count"]) for e in result["fiveLog"]] == [
        (888, True, 1),
        (1102, True, 2),  # 希儿 UP
        (1101, False, 1),  # 布洛妮娅歪
    ]


def test_analyse_sr_no_five(data_dir):
    """无五星：fiveLog 只有占位，统计全零（fiveAvg/isvalidNum/noWaiRate 为数字 0）"""
    result = ga.analyse(2, SR_UID, 12, "sr")
    stat = result["stat"]
    assert stat["allNum"] == 3
    assert stat["fiveNum"] == 0
    assert stat["noFiveNum"] == 3  # 全部抽数都是垫抽
    assert stat["fiveAvg"] == 0
    assert stat["isvalidNum"] == 0
    assert stat["upYs"] == "0"
    assert result["noWaiRate"] == 0
    assert len(result["fiveLog"]) == 1
    assert result["fiveLog"][0]["id"] == 888
    assert result["fiveLog"][0]["count"] == 3
    # 无四星以上的 maxFour 也正常返回（论剑）
    assert result["maxFour"]["name"] == "论剑"
    assert stat["noFourNum"] == 2
    assert stat["fourAvg"] == "1.00"


def test_analyse_empty(data_dir):
    assert ga.analyse(9, "999999999", 301, "gs") is None
    assert ga.stat(9, "999999999", "all", "gs") is None


# ---------------- stat：按版本统计 ----------------


def test_stat_gs_char(data_dir):
    result = ga.stat(1, GS_UID, "char", "gs")
    assert result["isMix"] is False
    versions = result["versionData"]
    assert [(v["version"], v["half"]) for v in versions] == [("4.2", "上半"), ("4.1", "上半")]
    v42, v41 = versions
    assert v42["from"] == "23-11-08"  # YY-MM-DD
    assert v42["to"] == "23-11-28"
    assert v42["name"] == "芙宁娜 / 白术"  # 当期 char5 简称拼接
    assert v42["upIds"] == {10000089: "芙宁娜"}
    assert v41["name"] == "那维 / 胡桃"
    # 4.1 上半：那维莱特 UP + 莫娜歪 + 行秋/匣里灭辰/弹弓/讨龙
    assert v41["stats"] == {
        "w5Num": 0,
        "w5UpNum": 0,
        "c5Num": 2,
        "c5UpNum": 1,
        "c4Num": 1,
        "w4Num": 1,
        "w3Num": 2,
        "upNum": 1,
        "star5Num": 2,
        "star4Num": 2,
        "totalNum": 6,
    }
    # items 按 star/num/isUp 降序（完全平票时 id 降序）
    assert [(i["id"], i["star"], i["isUp"]) for i in v42["items"]] == [
        (10000089, 5, 1),
        (11401, 4, 0),
        (13303, 3, 0),
        (12305, 3, 0),
    ]
    total = result["totalStat"]
    assert total["totalNum"] == 10
    assert total["upNum"] == 2
    assert total["avgUpNum"] == "5.0"  # 10/2


def test_stat_gs_all(data_dir):
    """all：hasVersion=False，全部记录归入单个 "全部统计" 桶"""
    result = ga.stat(1, GS_UID, "all", "gs")
    versions = result["versionData"]
    assert len(versions) == 1
    v = versions[0]
    assert v["version"] == "全部统计"
    assert v["from"] == "" and v["to"] == ""
    assert v["name"] == ""
    total = result["totalStat"]
    assert total["totalNum"] == 18  # 301(10) + 302(3) + 200(2) + 500(3)
    assert total["upNum"] == 0  # 全部统计无 UP 名单
    assert total["avgUpNum"] == 0  # upNum 为 0 时是数字 0


def test_stat_gs_normal(data_dir):
    result = ga.stat(1, GS_UID, "normal", "gs")
    v = result["versionData"][0]
    assert v["version"] == "常驻池"
    assert v["from"] == "" and v["to"] == ""


def test_stat_gs_mix(data_dir):
    result = ga.stat(1, GS_UID, "mix", "gs")
    assert result["isMix"] is True
    v = result["versionData"][0]
    assert v["version"] == "4.5"
    assert v["half"] == "上半"
    assert v["from"] == "24-03-13"
    assert v["name"] == "优菈 / 阿贝多 / 可莉"
    assert v["upIds"] == {12503: "松籁响起之时", 10000051: "优菈"}
    assert v["stats"]["upNum"] == 2
    assert result["totalStat"]["avgUpNum"] == "1.5"  # 3/2


def test_stat_sr_up(data_dir):
    """星铁 up：角色池 + 光锥池合并，按版本分桶；sr 角色无 abbr，name 为空串"""
    result = ga.stat(2, SR_UID, "up", "sr")
    versions = result["versionData"]
    assert len(versions) == 1
    v = versions[0]
    assert v["version"] == "1.0"
    assert v["half"] == "上半"
    assert v["name"] == ""  # sr 角色 data.json 无 abbr（JS 版 push undefined → join 空串）
    assert v["upIds"] == {1102: "希儿"}
    # 锋镝在角色池和光锥池各出 1 张，合并 num=2
    fengdi = next(i for i in v["items"] if i["id"] == "20000")
    assert fengdi["num"] == 2
    assert v["stats"]["totalNum"] == 7  # 11(4) + 12(3)
    assert v["stats"]["c5UpNum"] == 1
    assert result["totalStat"]["avgUpNum"] == "7.0"  # 7/1


def test_stat_sr_normal(data_dir):
    result = ga.stat(2, SR_UID, "normal", "sr")
    v = result["versionData"][0]
    assert v["version"] == "常驻池"
    assert v["stats"]["c5Num"] == 1  # 白露
    assert v["stats"]["w3Num"] == 1  # 嘉果
    assert result["totalStat"]["upNum"] == 0


def test_stat_sr_mix_empty(data_dir):
    """星铁没有集录池，stat mix 读 500 为空 → None"""
    assert ga.stat(2, SR_UID, "mix", "sr") is None


# ---------------- get_version 边界 ----------------


def test_get_version():
    inside = ga.get_version(datetime(2023, 10, 1), True, False, "gs")
    assert inside["version"] == "4.1"
    assert inside["half"] == "上半"
    # 卡池 from/to 边界是严格大于/小于，恰好等于 from 不匹配该池
    edge = ga.get_version(datetime(2023, 9, 27, 6, 0, 0), True, False, "gs")
    assert not (edge["version"] == "4.1" and edge["half"] == "上半")
    # 晚于所有已知卡池（含硬编码的"新版本"兜底区间）→ "未知"
    future = ga.get_version(datetime(2027, 1, 1), True, False, "gs")
    assert future["version"] == "未知"
    assert future["char5"] == []
    # has_version=False 时兜底为 "全部"
    assert ga.get_version(datetime(2023, 10, 1), False, False, "gs")["version"] == "全部"
    # 集录池优先于普通池
    mix = ga.get_version(datetime(2024, 3, 15), True, True, "gs")
    assert mix["version"] == "4.5"
    assert "优菈" in mix["char5"]


# ---------------- readJSON：去重与未知物品占位 ----------------


def test_read_json_dedup_and_unknown(tmp_path, monkeypatch):
    """同一记录 id 去重；未知名称的物品映射为占位 id（角色 404 / 武器 403）"""
    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)

    def rec(i, name, item_type, rank):
        return {"id": str(i), "name": name, "item_type": item_type, "rank_type": str(rank),
                "time": f"2023-10-0{i} 10:00:00", "gacha_type": "301"}

    store.write_gacha_log(1, GS_UID, 301, "gs", [
        rec(1, "莫娜", "角色", 5),
        rec(1, "莫娜", "角色", 5),  # 重复 id，应被去重
        rec(2, "不存在的角色", "角色", 5),
        rec(3, "不存在的武器", "武器", 5),
    ])
    data = ga.read_json_items(1, GS_UID, 301, "gs")
    assert len(data["items"]) == 3  # 去重后 3 条
    ids = {item["id"] for item in data["items"]}
    assert 10000041 in ids  # 莫娜
    assert 404 in ids  # 未知角色占位（star=4）
    assert 403 in ids  # 未知武器占位（star=3）
    assert data["itemMap"][404]["star"] == 4
    assert data["itemMap"][403]["star"] == 3
    # 按时间倒序
    times = [item["time"] for item in data["items"]]
    assert times == sorted(times, reverse=True)
