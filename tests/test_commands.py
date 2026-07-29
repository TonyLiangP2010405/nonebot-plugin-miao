"""命令层测试：matcher 注册冒烟 + 纯逻辑（UID/游戏判定、关键词、正则交叉命中）

注意：on_regex 等 matcher 创建要求 nonebot 已初始化，因此 commands 模块
统一在 fixture / 测试函数内 import（nonebug 的 autouse fixture 先于测试执行初始化）。
"""
import re
import time
from types import SimpleNamespace

import pytest

from nonebot_plugin_miao.core import store


@pytest.fixture
def cmds():
    """在 nonebot 初始化后导入 commands 模块（on_regex 注册要求已初始化）"""
    from nonebot_plugin_miao.commands import bind, common, gacha, profile

    return SimpleNamespace(bind=bind, common=common, gacha=gacha, profile=profile)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录重定向到临时目录"""
    d = tmp_path / "data"
    monkeypatch.setattr(store, "_data_dir", lambda: d)
    return d


class FakeEvent:
    """resolve_uid 只需 get_user_id / get_plaintext，用鸭子类型代替真实事件"""

    def __init__(self, user_id="12345", text=""):
        self._user_id = user_id
        self._text = text

    def get_user_id(self):
        return self._user_id

    def get_plaintext(self):
        return self._text


# ---------------------------------------------------------------------------
# 注册冒烟：所有 matcher 已注册到 nonebot
# ---------------------------------------------------------------------------


def test_matchers_registered(cmds):
    from nonebot.matcher import matchers

    all_matchers = set()
    for ms in matchers.values():
        all_matchers.update(ms)
    for m in (
        cmds.bind.bind_uid_m,
        cmds.bind.del_bind_m,
        cmds.bind.bind_cookie_m,
        cmds.bind.del_cookie_m,
        cmds.bind.my_bind_m,
        cmds.gacha.authkey_m,
        cmds.gacha.update_m,
        cmds.gacha.analyse_m,
        cmds.gacha.stat_m,
        cmds.gacha.simulate_m,
        cmds.gacha.bing_m,
        cmds.gacha.import_m,
        cmds.gacha.export_m,
        cmds.profile.update_m,
        cmds.profile.list_m,
        cmds.profile.artis_list_m,
        cmds.profile.detail_m,
    ):
        assert m in all_matchers


# ---------------------------------------------------------------------------
# common：resolve_uid / is_sr / game_of
# ---------------------------------------------------------------------------


def test_resolve_uid_text_priority(cmds, data_dir):
    store.bind_uid("12345", "gs", "100000001")
    event = FakeEvent("12345", "#抽卡记录 100234567")
    # 消息文本里的 UID 优先于绑定
    assert cmds.common.resolve_uid(event, "#抽卡记录 100234567", "gs") == "100234567"


def test_resolve_uid_fallback_binding(cmds, data_dir):
    store.bind_uid("12345", "gs", "100000001")
    event = FakeEvent("12345", "#抽卡记录")
    assert cmds.common.resolve_uid(event, "#抽卡记录", "gs") == "100000001"


def test_resolve_uid_unbound(cmds, data_dir):
    event = FakeEvent("99999", "#抽卡记录")
    assert cmds.common.resolve_uid(event, "#抽卡记录", "gs") is None


def test_is_sr_and_game_of(cmds):
    assert cmds.common.is_sr("#星铁抽卡记录") is True
    assert cmds.common.is_sr("#抽卡记录") is False
    assert cmds.common.game_of("#星铁更新抽卡记录") == "sr"
    assert cmds.common.game_of("#抽卡记录") == "gs"


# ---------------------------------------------------------------------------
# gacha：关键词归一化与卡池标题
# ---------------------------------------------------------------------------


def test_pool_label_of(cmds):
    assert cmds.gacha.pool_label_of("角色", "gs") == "角色活动祈愿"
    assert cmds.gacha.pool_label_of("武器", "gs") == "武器活动祈愿"
    assert cmds.gacha.pool_label_of("常驻", "gs") == "常驻祈愿"
    assert cmds.gacha.pool_label_of("集录", "gs") == "集录祈愿"
    assert cmds.gacha.pool_label_of("光锥", "sr") == "光锥活动跃迁"
    assert cmds.gacha.pool_label_of("角色", "sr") == "角色活动跃迁"
    assert cmds.gacha.pool_label_of("不存在的池", "gs") is None


def test_analyse_keyword(cmds):
    assert cmds.gacha.analyse_keyword("#抽卡记录", "gs") == "抽卡"
    assert cmds.gacha.analyse_keyword("#星铁角色记录", "sr") == "角色"
    # 星铁没有"抽卡"关键词，归入角色池
    assert cmds.gacha.analyse_keyword("#星铁抽卡记录", "sr") == "角色"
    assert cmds.gacha.analyse_keyword("#集录祈愿", "gs") == "集录"
    assert cmds.gacha.analyse_keyword("抽卡分析", "gs") == "抽卡"
    # 原神没有光锥池
    assert cmds.gacha.analyse_keyword("#光锥记录", "gs") is None


def test_stat_keyword(cmds):
    assert cmds.gacha.stat_keyword("#全部统计") == "全部"
    assert cmds.gacha.stat_keyword("#版本统计") == "全部"
    assert cmds.gacha.stat_keyword("#星铁常驻池统计") == "常驻"
    assert cmds.gacha.stat_keyword("#光锥统计") == "光锥"


def test_sim_kind_and_single(cmds):
    assert cmds.gacha.sim_kind_of("#十连") == "role"
    assert cmds.gacha.sim_kind_of("#十连2") == "role2"
    assert cmds.gacha.sim_kind_of("#武器十连") == "weapon"
    assert cmds.gacha.sim_kind_of("#常驻十连") == "permanent"
    assert cmds.gacha.sim_kind_of("#单抽") == "role"
    assert cmds.gacha.is_single("#单抽") is True
    assert cmds.gacha.is_single("#十连") is False


def test_pool_new_summary(cmds):
    assert cmds.gacha.pool_new_summary({301: 5, 302: 0}, "gs") == "角色池新增 5 条\n武器池新增 0 条"
    assert cmds.gacha.pool_new_summary({301: 0}, "gs") == "没有新增记录（本地已是最新）"


# ---------------------------------------------------------------------------
# 正则交叉命中：#十连 系列与 #抽卡记录/统计 系列互不误伤
# ---------------------------------------------------------------------------


def test_simulate_regex(cmds):
    for text in ("#十连", "#十连2", "#武器十连", "#常驻十连", "#单抽", "十连", "#10连", "#抽卡", "#抽奖"):
        assert re.match(cmds.gacha.RE_SIMULATE, text), text
    # 不误伤记录/统计指令和普通聊天
    for text in ("#抽卡记录", "#抽卡分析", "#抽卡统计", "#星铁更新抽卡记录", "今天十连真欧", "#十连抽"):
        assert not re.match(cmds.gacha.RE_SIMULATE, text), text


def test_analyse_regex(cmds):
    for text in ("#抽卡记录", "抽卡记录", "#星铁光锥分析", "#角色祈愿", "#武器池记录", "#up记录"):
        assert re.match(cmds.gacha.RE_ANALYSE, text), text
    for text in ("#十连", "#单抽", "#抽卡统计", "#更新抽卡记录", "#绑定uid 100000001"):
        assert not re.match(cmds.gacha.RE_ANALYSE, text), text


def test_stat_regex(cmds):
    for text in ("#全部统计", "#版本统计", "#星铁常驻池统计", "#抽卡统计"):
        assert re.match(cmds.gacha.RE_STAT, text), text
    for text in ("#抽卡记录", "#十连", "#全部记录"):
        assert not re.match(cmds.gacha.RE_STAT, text), text


def test_update_and_bind_regex(cmds):
    assert re.match(cmds.gacha.RE_UPDATE, "#更新抽卡记录")
    assert re.match(cmds.gacha.RE_UPDATE, "#星铁更新抽卡记录")
    assert not re.match(cmds.gacha.RE_UPDATE, "#更新抽卡")
    assert re.match(cmds.gacha.RE_IMPORT, "#导入记录 https://example.com/uigf.json")
    assert re.match(cmds.gacha.RE_EXPORT, "#星铁导出记录")
    # 绑定系列互不命中
    bind = cmds.bind
    assert not re.match(bind.RE_BIND_UID, "#绑定cookie abc")
    assert not re.match(bind.RE_BIND_COOKIE, "#绑定uid 100000001")
    assert not re.match(bind.RE_MY_BIND, "#删除绑定")
    assert re.match(bind.RE_BIND_UID, "#星铁绑定uid 800000001")


# ---------------------------------------------------------------------------
# profile：面板指令正则交叉命中
# ---------------------------------------------------------------------------


def test_profile_update_regex(cmds):
    p = cmds.profile
    for text in ("#更新面板", "#面板更新", "#星铁更新面板", "#原神更新面板", "#全部面板更新",
                 "#更新全部面板", "#获取游戏角色详情", "#更新面板 800055548"):
        assert re.match(p.RE_UPDATE, text), text
    for text in ("#面板列表", "#优菈面板", "#更新面板数据", "#圣遗物列表"):
        assert not re.match(p.RE_UPDATE, text), text


def test_profile_list_regex(cmds):
    p = cmds.profile
    for text in ("#面板列表", "#面板", "#角色面板", "#星铁面板角色", "#星铁面板列表", "#面板列表 800055548"):
        assert re.match(p.RE_LIST, text), text
    for text in ("#更新面板", "#优菈面板", "#圣遗物列表"):
        assert not re.match(p.RE_LIST, text), text


def test_profile_artis_list_regex(cmds):
    p = cmds.profile
    for text in ("#圣遗物列表", "#星铁遗器列表", "#圣遗物列表 800055548"):
        assert re.match(p.RE_ARTIS_LIST, text), text
    for text in ("#圣遗物", "#面板列表", "#优菈圣遗物"):
        assert not re.match(p.RE_ARTIS_LIST, text), text


def test_profile_detail_regex(cmds):
    p = cmds.profile
    for text in ("#优菈面板", "#刻晴面板", "#优菈圣遗物", "#星铁镜流面板", "#镜流遗器", "#优菈面板 800055548"):
        assert re.match(p.RE_DETAIL, text), text
    # 角色名解析：group(1) 为角色名（可带游戏前缀，处理时剥离）
    assert re.match(p.RE_DETAIL, "#刻晴面板").group(1) == "刻晴"
    assert re.match(p.RE_DETAIL, "#星铁镜流面板").group(1) == "星铁镜流"
    # 不与其他面板指令冲突
    for text in ("#面板列表", "#更新面板", "#星铁更新面板", "#面板", "#圣遗物列表", "#角色面板"):
        assert not re.match(p.RE_DETAIL, text), text


@pytest.mark.asyncio
async def test_my_bind_unbound(app, cmds, data_dir):
    from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, Message
    from nonebot.adapters.onebot.v11.event import Sender

    async with app.test_matcher(cmds.bind.my_bind_m) as ctx:
        adapter = ctx.create_adapter(base=Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="10000")
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=10000,
            post_type="message",
            sub_type="normal",
            user_id=12345,
            group_id=88888,
            message_type="group",
            message_id=1,
            message=Message("#我的绑定"),
            original_message=Message("#我的绑定"),
            raw_message="#我的绑定",
            font=0,
            sender=Sender(user_id=12345, nickname="tester"),
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "tester 的绑定信息：\n原神 UID：未绑定\n星铁 UID：未绑定\n米游社 cookie：未绑定",
        )
        ctx.should_finished()
