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
    from nonebot_plugin_miao.commands import bind, common, encyclopedia, gacha, help, profile, strategy

    return SimpleNamespace(
        bind=bind,
        common=common,
        encyclopedia=encyclopedia,
        gacha=gacha,
        help=help,
        profile=profile,
        strategy=strategy,
    )


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
        cmds.encyclopedia.encyclopedia_m,
        cmds.strategy.strategy_help_m,
        cmds.strategy.strategy_setting_m,
        cmds.strategy.strategy_m,
        cmds.help.profile_help_m,
        cmds.help.gacha_help_m,
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
# 正则交叉命中：模拟抽卡与带斜杠的记录/统计指令互不误伤
# ---------------------------------------------------------------------------


def test_simulate_regex(cmds):
    for text in ("#十连", "/十连", "#十连2", "#武器十连", "#常驻十连", "#单抽", "十连", "#10连", "#抽卡", "#抽奖"):
        assert re.match(cmds.gacha.RE_SIMULATE, text), text
    # 不误伤记录/统计指令和普通聊天
    for text in ("#抽卡记录", "#抽卡分析", "#抽卡统计", "#星铁更新抽卡记录", "今天十连真欧", "#十连抽"):
        assert not re.match(cmds.gacha.RE_SIMULATE, text), text


def test_analyse_regex(cmds):
    for text in ("/抽卡记录", "/星铁光锥分析", "/角色祈愿", "/武器池记录", "/up记录"):
        assert re.match(cmds.gacha.RE_ANALYSE, text), text
    for text in ("抽卡记录", "#抽卡记录", "#十连", "#单抽", "/抽卡统计", "/更新抽卡记录", "/绑定uid 100000001"):
        assert not re.match(cmds.gacha.RE_ANALYSE, text), text


def test_stat_regex(cmds):
    for text in ("/全部统计", "/版本统计", "/星铁常驻池统计", "/抽卡统计"):
        assert re.match(cmds.gacha.RE_STAT, text), text
    for text in ("全部统计", "#全部统计", "/抽卡记录", "#十连", "/全部记录"):
        assert not re.match(cmds.gacha.RE_STAT, text), text


def test_update_and_bind_regex(cmds):
    assert re.match(cmds.gacha.RE_UPDATE, "/更新抽卡记录")
    assert re.match(cmds.gacha.RE_UPDATE, "/星铁更新抽卡记录")
    assert not re.match(cmds.gacha.RE_UPDATE, "更新抽卡记录")
    assert not re.match(cmds.gacha.RE_UPDATE, "#更新抽卡记录")
    assert not re.match(cmds.gacha.RE_UPDATE, "/更新抽卡")
    assert re.match(cmds.gacha.RE_IMPORT, "/导入记录 https://example.com/uigf.json")
    assert re.match(cmds.gacha.RE_EXPORT, "/星铁导出记录")
    assert re.match(cmds.gacha.RE_AUTHKEY, "/https://example.com/?authkey=abc")
    assert not re.match(cmds.gacha.RE_AUTHKEY, "https://example.com/?authkey=abc")
    # 绑定系列互不命中
    bind = cmds.bind
    assert not re.match(bind.RE_BIND_UID, "/绑定cookie abc")
    assert not re.match(bind.RE_BIND_COOKIE, "/绑定uid 100000001")
    assert not re.match(bind.RE_MY_BIND, "/删除绑定")
    assert re.match(bind.RE_BIND_UID, "/星铁绑定uid 800000001")
    assert not re.match(bind.RE_BIND_UID, "星铁绑定uid 800000001")
    assert not re.match(bind.RE_BIND_UID, "#星铁绑定uid 800000001")


def test_help_requires_slash(cmds):
    assert re.match(cmds.help.RE_PROFILE_HELP, "/面板帮助")
    assert re.match(cmds.help.RE_GACHA_HELP, "/抽卡帮助")
    for text in ("面板帮助", "#面板帮助"):
        assert not re.match(cmds.help.RE_PROFILE_HELP, text)
    for text in ("抽卡帮助", "#抽卡帮助"):
        assert not re.match(cmds.help.RE_GACHA_HELP, text)


# ---------------------------------------------------------------------------
# encyclopedia：角色/武器图鉴触发与查询词
# ---------------------------------------------------------------------------


def test_encyclopedia_regex_and_query(cmds):
    e = cmds.encyclopedia
    cases = {
        "/芙宁娜图鉴": "芙宁娜",
        "#雾切图鉴": "雾切",
        "/原神刻晴图鉴": "刻晴",
        "/图鉴 芙宁娜": "芙宁娜",
        "/角色图鉴": "角色",
        "/武器图鉴": "武器",
        "/角色索引": "角色",
        "/武器列表": "武器",
        "/图鉴": "",
        "/图鉴 帮助": "帮助",
    }
    for text, query in cases.items():
        assert re.match(e.RE_ENCYCLOPEDIA, text), text
        assert e.parse_encyclopedia_query(text) == query
    for text in ("芙宁娜图鉴", "今天看了芙宁娜图鉴", "/芙宁娜面板", "/图鉴芙宁娜"):
        assert not re.match(e.RE_ENCYCLOPEDIA, text), text


def test_encyclopedia_lookup(cmds):
    find = cmds.encyclopedia.find_encyclopedia_entry
    kind, character = find("水神")
    assert kind == "character" and character.name == "芙宁娜"
    kind, weapon = find("雾切")
    assert kind == "weapon" and weapon["name"] == "雾切之回光"
    assert find("不存在的图鉴条目") is None


# ---------------------------------------------------------------------------
# strategy：原神、星铁与绝区零攻略指令与来源解析
# ---------------------------------------------------------------------------


def test_strategy_regex_and_query(cmds):
    strategy = cmds.strategy
    cases = {
        "/心海攻略": (False, "gs", "心海", None),
        "#心海攻略4": (False, "gs", "心海", 4),
        "/原神心海攻略7": (False, "gs", "心海", 7),
        "/更新早柚攻略2": (True, "gs", "早柚", 2),
        "/星铁流萤攻略": (False, "sr", "流萤", None),
        "/铁道流萤攻略": (False, "sr", "流萤", None),
        "#崩铁饮月君攻略3": (False, "sr", "饮月君", 3),
        "/更新星穹铁道姬子启行攻略2": (True, "sr", "姬子启行", 2),
        "/绝区零星见雅攻略": (False, "zzz", "星见雅", None),
        "#更新ZZZ艾莲攻略4": (True, "zzz", "艾莲", 4),
    }
    for text, expected in cases.items():
        assert re.match(strategy.RE_STRATEGY, text), text
        assert strategy.parse_strategy_query(text) == expected

    for text in ("/攻略帮助", "#攻略说明", "/攻略", "/星铁攻略帮助", "#绝区零攻略说明"):
        assert re.match(strategy.RE_STRATEGY_HELP, text), text
        assert strategy.parse_strategy_query(text) is None
    for text in (
        "/设置默认攻略1",
        "#设置默认攻略7",
        "/设置默认攻略",
        "/设置星铁默认攻略3",
        "#设置绝区零默认攻略4",
    ):
        assert re.match(strategy.RE_STRATEGY_SETTING, text), text
    assert strategy.parse_strategy_help_game("/星铁攻略帮助") == "sr"
    assert strategy.parse_strategy_help_game("/攻略帮助") is None
    assert strategy.parse_strategy_setting("/设置绝区零默认攻略4") == ("zzz", 4)
    for text in ("心海攻略", "今天看了心海攻略", "/心海攻略图"):
        assert not re.match(strategy.RE_STRATEGY, text), text


def test_strategy_help_lists_all_sources(cmds):
    text = cmds.strategy.strategy_help_text()
    assert "1——西风驿站" in text
    assert "7——婧枫赛赛" in text
    assert "星铁攻略来源" in text and "3——丶ATRI丶" in text
    assert "绝区零攻略来源" in text and "4——小橙子阿" in text
    sr_text = cmds.strategy.strategy_help_text("sr")
    assert "星铁攻略来源" in sr_text
    assert "原神攻略来源" not in sr_text


def test_strategy_role_resolution(cmds):
    resolve = cmds.strategy._resolve_role_name
    assert resolve("饮月君", "sr") == "丹恒•饮月"
    assert resolve("水神", "gs") == "芙宁娜"
    assert resolve("星见雅", "zzz") == "星见雅"
    assert resolve("不存在的铁道角色", "sr") is None


# ---------------------------------------------------------------------------
# profile：面板指令正则交叉命中
# ---------------------------------------------------------------------------


def test_profile_update_regex(cmds):
    p = cmds.profile
    for text in ("/更新面板", "/面板更新", "/星铁更新面板", "/原神更新面板", "/全部面板更新",
                 "/更新全部面板", "/获取游戏角色详情", "/更新面板 800055548"):
        assert re.match(p.RE_UPDATE, text), text
    for text in ("更新面板", "#更新面板", "/面板列表", "/优菈面板", "/更新面板数据", "/圣遗物列表"):
        assert not re.match(p.RE_UPDATE, text), text


def test_profile_list_regex(cmds):
    p = cmds.profile
    for text in ("/面板列表", "/面板", "/角色面板", "/星铁面板角色", "/星铁面板列表", "/面板列表 800055548"):
        assert re.match(p.RE_LIST, text), text
    for text in ("面板列表", "#面板列表", "/更新面板", "/优菈面板", "/圣遗物列表"):
        assert not re.match(p.RE_LIST, text), text


def test_profile_artis_list_regex(cmds):
    p = cmds.profile
    for text in ("/圣遗物列表", "/星铁遗器列表", "/圣遗物列表 800055548"):
        assert re.match(p.RE_ARTIS_LIST, text), text
    for text in ("圣遗物列表", "#圣遗物列表", "/圣遗物", "/面板列表", "/优菈圣遗物"):
        assert not re.match(p.RE_ARTIS_LIST, text), text


def test_profile_detail_regex(cmds):
    p = cmds.profile
    for text in ("/优菈面板", "/刻晴面板", "/优菈圣遗物", "/星铁镜流面板", "/镜流遗器", "/优菈面板 800055548"):
        assert re.match(p.RE_DETAIL, text), text
    # 角色名解析：group(1) 为角色名（可带游戏前缀，处理时剥离）
    assert re.match(p.RE_DETAIL, "/刻晴面板").group(1) == "刻晴"
    assert re.match(p.RE_DETAIL, "/星铁镜流面板").group(1) == "星铁镜流"
    # 不与其他面板指令冲突
    other_commands = (
        "刻晴面板", "#刻晴面板", "/面板列表", "/更新面板", "/星铁更新面板",
        "/面板", "/圣遗物列表", "/角色面板",
    )
    for text in other_commands:
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
            message=Message("/我的绑定"),
            original_message=Message("/我的绑定"),
            raw_message="/我的绑定",
            font=0,
            sender=Sender(user_id=12345, nickname="tester"),
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "tester 的绑定信息：\n原神 UID：未绑定\n星铁 UID：未绑定\n米游社 cookie：未绑定",
        )
        ctx.should_finished()


@pytest.mark.parametrize("text,game,kind,single", [
    ("十抽2", "gs", "role2", False),
    ("原神武器十连", "gs", "weapon", False),
    ("星铁十连2", "sr", "role2", False),
    ("/铁道光锥单抽2", "sr", "weapon2", True),
    ("#星穹铁道常驻10抽", "sr", "permanent", False),
    ("绝区零十连2", "zzz", "role2", False),
    ("ZZZ音擎十连2", "zzz", "weapon2", False),
    ("绝区零常驻单抽", "zzz", "permanent", True),
    ("星铁角色十连12", "sr", "role12", False),
])
def test_multigame_simulation_commands(cmds, text, game, kind, single):
    g = cmds.gacha
    assert re.fullmatch(g.RE_SIMULATE, text)
    assert g.sim_game_of(text) == game
    assert g.sim_kind_of(text) == kind
    assert g.is_single(text) == single
    assert not re.match(g.RE_ANALYSE, text)
    assert not re.match(g.RE_STAT, text)


def test_simulation_invalid_commands(cmds):
    for text in ("星铁十连0", "武器十连常驻"):
        with pytest.raises(ValueError):
            cmds.gacha.sim_kind_of(text)
    for text in ("/绝区零抽卡记录", "今天星铁十连出金", "星铁十连2次"):
        assert not re.fullmatch(cmds.gacha.RE_SIMULATE, text)


def test_pool_list_commands_and_content(cmds, sim_data):
    for game, prefix in (("gs", ""), ("sr", "星铁"), ("zzz", "绝区零")):
        for text in (f"/{prefix}卡池列表", f"/{prefix}当前卡池", f"/更新{prefix}卡池", f"/{prefix}更新卡池"):
            assert re.fullmatch(cmds.gacha.RE_SIM_POOLS, text)
            assert cmds.gacha.sim_game_of(text) == game
        text = cmds.gacha.sim_pool_list_text(game, sim_data[game])
        assert prefix + "十连2" in text
        assert sim_data[game]["pools"][0]["title"] in text
    for text in ("卡池列表", "#星铁卡池列表", "/更新面板资源", "/更新星铁记录"):
        assert not re.fullmatch(cmds.gacha.RE_SIM_POOLS, text)
    for text in ("/定轨", "/定轨1", "/定轨2", "/定轨0", "/定轨取消"):
        assert re.fullmatch(cmds.gacha.RE_BING, text)


@pytest.mark.parametrize("private", [False, True])
async def test_simulation_handler_single_draw_real_state(cmds, app, sim_data, monkeypatch, private):
    from nonebot.adapters.onebot.v11 import (
        Adapter,
        Bot,
        GroupMessageEvent,
        Message,
        PrivateMessageEvent,
    )
    from nonebot.adapters.onebot.v11.event import Sender

    from nonebot_plugin_miao.gacha import simulate

    async def ensure(game):
        return sim_data[game]

    rendered = []

    async def render(result, name):
        rendered.append(result)
        return b"test-image"

    monkeypatch.setattr(cmds.gacha.sim_pools, "ensure_pools", ensure)
    monkeypatch.setattr(cmds.gacha, "render_gacha_trial", render)
    message = "绝区零音擎单抽2"
    cls = PrivateMessageEvent if private else GroupMessageEvent
    event = cls(
        time=int(time.time()), self_id=123456, post_type="message", sub_type="friend" if private else "normal",
        user_id=12345, **({} if private else {"group_id": 88888}),
        message_type="private" if private else "group", message_id=1,
        message=Message(message), original_message=Message(message), raw_message=message, font=0,
        sender=Sender(user_id=12345, nickname="tester"),
    )
    from nonebot.adapters.onebot.v11 import MessageSegment

    async with app.test_matcher(cmds.gacha.simulate_m) as ctx:
        adapter = ctx.create_adapter(base=Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="123456")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, MessageSegment.image(b"test-image"))
        ctx.should_finished()
    assert len(rendered) == 1
    result = rendered[0]
    assert result["game"] == "zzz" and len(result["list"]) == 1
    assert "音擎池" in result["poolName"]
    scope = "private:12345" if private else "88888:12345"
    assert simulate.load_user(scope, "zzz")["today"]["weaponNum"] == 1
