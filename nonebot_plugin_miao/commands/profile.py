"""面板指令：
/更新面板 / /面板列表 / /角色名面板（含圣遗物评分）/ /圣遗物列表

面板属性计算见 core/attr_calc.py，评分见 core/artis_mark.py。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nonebot import on_message, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment

from ..core import meta, store
from ..core.artis_mark import calc_mark, format_value, key_title
from ..core.attr_calc import calc_attr, elem_name
from ..core.panel_quota import (
    FEATURE_DETAIL,
    FEATURE_RANK,
    adjust_panel_draws,
    authorize_panel_call,
    clear_pending,
    confirm_timeout,
    has_pending,
    is_superuser,
    mark_update,
    panel_enabled,
    panel_enabled_source,
    panel_status,
    pop_pending,
    register_pending,
    reserve_panel_update,
    set_panel_enabled,
    update_limit_message,
    update_wait,
)
from ..core.player import Player
from ..core.profile_rank import MODE_DMG, MODE_MARK, collect_rank
from ..datasource.mys import update_profile_mys
from ..datasource.profile_service import update_profile
from ..dmg import calc_dmg
from ..render import (
    render_artis_list,
    render_profile_detail,
    render_profile_list,
    render_profile_rank,
    render_profile_update,
)
from ..render.profile_detail import dmg_row_text
from .common import ensure_panel_available, ensure_panel_enabled, game_of, guard, resolve_uid, send_image

_GAME_NAME = {"gs": "原神", "sr": "星铁"}
_UPDATES_IN_FLIGHT: set[tuple[str, str]] = set()
# 部位简称（花羽沙杯冠 / 头手衣鞋球绳）
_POS_NAME = {"gs": ["花", "羽", "沙", "杯", "冠"], "sr": ["头", "手", "衣", "鞋", "球", "绳"]}

# 正则常量（导出以便测试交叉命中）
RE_UPDATE = r"^/(星铁|原神)?(全部面板更新|更新全部面板|获取游戏角色详情|更新面板|面板更新)\s*(\d{9,10})?$"
RE_MYS_UPDATE = r"^/(星铁|原神)?(米游社|mys)(更新面板|面板更新)\s*(\d{9,10})?$"
RE_LIST = r"^/(星铁|原神)?(面板角色|角色面板|面板)(列表)?\s*(\d{9,10})?$"
RE_ARTIS_LIST = r"^/(星铁|原神)?(圣遗物|遗器)列表\s*(\d{9,10})?$"
RE_PANEL_QUOTA = r"^/面板次数$"
RE_PANEL_ADJUST = r"^/(群面板排名次数调整|面板次数调整)(?:\s|$)"
RE_PANEL_SWITCH = r"^/面板开关(?:\s+(开|关|启用|停用|重置|on|off))?$"
RE_RANK = r"^/(星铁|原神)?(.*?)(排名|排行|榜单)\s*(\d{9,10})?$"
# 角色名面板：负向前瞻排除 更新面板/面板列表/圣遗物列表/米游社更新面板 等指令（另有优先级兜底）
RE_DETAIL = (
    r"^/(?!(?:星铁|原神)?(?:(?:米游社|mys)(?:更新面板|面板更新)|全部面板更新|更新全部面板|获取游戏角色详情|"
    r"更新面板|面板更新|面板角色|角色面板|面板|面板列表|圣遗物列表|遗器列表)\s*(?:\d{9,10})?$)"
    r"([^#/]+?)\s*(详细|详情|面板|面版|圣遗物|遗器|伤害(?:[1-9]+\d*)?)\s*(\d{9,10})?$"
)

def _panel_command(pattern: str) -> Callable[[MessageEvent], bool]:
    """匹配面板指令文本，保留 OneBot 的 @ 消息段供处理器鉴权。"""
    command = re.compile(pattern)

    def matches(event: MessageEvent) -> bool:
        return all(segment.type in ("text", "at") for segment in event.get_message()) and (
            command.fullmatch(event.get_plaintext().strip()) is not None
        )

    return matches


update_m = on_message(rule=_panel_command(RE_UPDATE), priority=5, block=True)
mys_update_m = on_message(rule=_panel_command(RE_MYS_UPDATE), priority=5, block=True)
list_m = on_message(rule=_panel_command(RE_LIST), priority=5, block=True)
artis_list_m = on_regex(RE_ARTIS_LIST, priority=5, block=True)
quota_m = on_regex(RE_PANEL_QUOTA, priority=5, block=True)
adjust_m = on_regex(RE_PANEL_ADJUST, priority=5, block=True)
switch_m = on_regex(RE_PANEL_SWITCH, priority=5, block=True)
rank_m = on_regex(RE_RANK, priority=5, block=True)
# 角色名面板为正则兜底，优先级低于上面三条，避免吃掉更新、列表指令
detail_m = on_message(rule=_panel_command(RE_DETAIL), priority=10, block=True)


async def _panel_confirm_rule(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    text = event.get_plaintext().strip()
    if text in ("/面板确认", "/面板取消"):
        return True
    return text in ("确认", "取消") and has_pending(event.group_id, event.get_user_id(), confirm_timeout())


# 在 NoneBot 等待会话回复的临时 matcher（优先级 0）之前处理面板确认。
confirm_m = on_message(priority=-1, block=True, rule=_panel_confirm_rule)


def _bind_tip(game: str) -> str:
    return f"请先绑定{_GAME_NAME[game]} UID：/{'星铁' if game == 'sr' else ''}绑定uid <UID>"


def _update_tip(game: str, target: str | None = None) -> str:
    command = f"/{'星铁' if game == 'sr' else ''}更新面板"
    if target is not None:
        command += " @群友"
    return f"暂无本地面板数据，请先发送 {command}"


def _char_not_found(name: str, game: str) -> str:
    tips = meta.suggest_characters(name, game)
    hint = f"，你是不是想找：{'、'.join(tips)}" if tips else ""
    return f"未找到角色【{name}】{hint}。支持角色名、别名，也会尝试纠正常见错字。"


def _mentioned_panel_user(event: MessageEvent) -> str | None:
    targets = [str(segment.data.get("qq", "")) for segment in event.get_message() if segment.type == "at"]
    if not targets:
        return None
    if not isinstance(event, GroupMessageEvent) or not is_superuser(event.get_user_id()):
        raise ValueError("仅 SUPERUSER 可以在群里 @群友 更新或查看对方的面板。")
    if len(targets) != 1 or not re.fullmatch(r"[1-9][0-9]*", targets[0]):
        raise ValueError("请只 @一位群友。")
    return targets[0]


def _panel_uid(event: MessageEvent, text: str, game: str, target: str | None) -> str:
    if target is None:
        uid = resolve_uid(event, text, game)
        if not uid:
            raise ValueError(_bind_tip(game))
    else:
        uid = store.get_uid(target, game)
        if not uid:
            raise ValueError(f"该群友还没有绑定{_GAME_NAME[game]} UID。")
    return uid


# ---------------------------------------------------------------------------
# /更新面板
# ---------------------------------------------------------------------------


@update_m.handle()
@guard(update_m)
async def _(event: MessageEvent):
    await _run_panel_update(update_m, event)
    await update_m.finish()


# ---------------------------------------------------------------------------
# /米游社更新面板
# ---------------------------------------------------------------------------


@mys_update_m.handle()
@guard(mys_update_m)
async def _(event: MessageEvent):
    await _run_panel_update(mys_update_m, event, use_mys=True)
    await mys_update_m.finish()


async def _run_panel_update(matcher, event: MessageEvent, *, use_mys: bool = False) -> None:
    await ensure_panel_enabled(matcher)
    target = _mentioned_panel_user(event)
    if use_mys and target is not None:
        raise ValueError("替群友更新请用 /更新面板 @群友 或 /星铁更新面板 @群友。")
    if isinstance(event, GroupMessageEvent):
        store.mark_panel_group(event.group_id)
    text = event.get_plaintext()
    game = game_of(text)
    user_id = target or event.get_user_id()
    uid = _panel_uid(event, text, game, target)
    key = (user_id, game)
    if key in _UPDATES_IN_FLIGHT:
        raise ValueError("该玩家的面板正在更新，请稍候。")
    _UPDATES_IN_FLIGHT.add(key)
    try:
        wait = update_wait(user_id, game)
        if wait > 0:
            raise ValueError(update_limit_message(wait))
        fetch = update_profile_mys if use_mys else update_profile
        async with reserve_panel_update(event):
            ret = await fetch(user_id, uid, game)
            if ret["code"] == "no_cookie":
                raise ValueError(ret["msg"])
            if ret["code"] == "cd":
                raise ValueError(f"面板数据更新冷却中，请 {ret['wait']} 秒后再试")
        mark_update(user_id, game)
    finally:
        _UPDATES_IN_FLIGHT.remove(key)

    player: Player = ret["player"]
    source = "米游社" if use_mys else player.data_source
    await send_image(
        matcher,
        await render_profile_update(player, game, ret["new_chars"], uid, source),
    )


# ---------------------------------------------------------------------------
# /面板列表
# ---------------------------------------------------------------------------


@list_m.handle()
@guard(list_m)
async def _(event: MessageEvent):
    target = _mentioned_panel_user(event)
    if target is not None:
        await ensure_panel_enabled(list_m)
        store.mark_panel_group(event.group_id)
    else:
        await ensure_panel_available(list_m, event)
    text = event.get_plaintext()
    game = game_of(text)
    uid = _panel_uid(event, text, game, target)
    player = Player.load(uid, game)
    if not player.avatars:
        await list_m.finish(_update_tip(game, target))
    await send_image(list_m, await render_profile_list(player, game))
    await list_m.finish()


# ---------------------------------------------------------------------------
# /面板次数
# ---------------------------------------------------------------------------


@quota_m.handle()
@guard(quota_m)
async def _(event: MessageEvent):
    await ensure_panel_enabled(quota_m)
    if isinstance(event, GroupMessageEvent):
        store.mark_panel_group(event.group_id)
    await quota_m.finish(await panel_status(event))


@adjust_m.handle()
@guard(adjust_m)
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await adjust_m.finish("请在对应群聊里调整面板次数。")
    if not is_superuser(event.get_user_id()):
        await adjust_m.finish("仅 SUPERUSER 可以调整面板次数。")

    parts = event.get_plaintext().split(maxsplit=1)
    command, amount = parts[0], parts[1] if len(parts) == 2 else ""
    targets = [str(segment.data.get("qq", "")) for segment in event.get_message() if segment.type == "at"]
    if len(targets) != 1 or not re.fullmatch(r"[1-9][0-9]*", targets[0]):
        await adjust_m.finish("请 @一位群友，例如 /面板次数调整 @群友 +2。")
    if not re.fullmatch(r"[+-][1-9][0-9]{0,3}", amount.strip()):
        await adjust_m.finish("请填写带正负号的次数（1–9999），例如 +2 或 -1。")

    feature = FEATURE_RANK if command == "/群面板排名次数调整" else FEATURE_DETAIL
    result = await adjust_panel_draws(event.group_id, targets[0], feature, int(amount))
    await adjust_m.finish(MessageSegment.at(targets[0]) + f" {result}")


@switch_m.handle()
@guard(switch_m)
async def _(event: MessageEvent):
    if not is_superuser(event.get_user_id()):
        await switch_m.finish("仅 SUPERUSER 可以切换面板开关")
    m = re.match(RE_PANEL_SWITCH, event.get_plaintext())
    action = ((m.group(1) if m else "") or "").lower()
    if not action:
        state = "开启" if panel_enabled() else "关闭"
        await switch_m.finish(
            f"面板功能当前：{state}（来源：{panel_enabled_source()}）\n"
            "用法：/面板开关 开｜/面板开关 关｜/面板开关 重置（恢复配置默认值）"
        )
    if action in ("开", "启用", "on"):
        set_panel_enabled(True)
        await switch_m.finish("面板功能已开启")
    if action in ("关", "停用", "off"):
        cancelled = set_panel_enabled(False)
        tip = f"，已取消 {cancelled} 个待确认请求" if cancelled else ""
        await switch_m.finish(f"面板功能已关闭{tip}，发送 /面板开关 开 可恢复")
    set_panel_enabled(None)
    state = "开启" if panel_enabled() else "关闭"
    await switch_m.finish(f"已恢复配置默认值，当前面板：{state}")


# ---------------------------------------------------------------------------
# /角色名面板（详细/详情/面板/面版/圣遗物/遗器/伤害）
# ---------------------------------------------------------------------------


def _fmt_base_plus(attr: dict[str, Any], key: str) -> str:
    """数值展示：总值（白值+加成）"""
    base = attr.get(f"{key}Base") or 0
    total = attr.get(key) or 0
    return f"{round(total)}（{round(base)}+{round(total - base)}）"


def _fmt_panel_text(avatar: dict[str, Any], attr: dict[str, Any], mark: dict[str, Any], game: str) -> str:
    char = meta.get_character(avatar.get("id") or avatar.get("name"), game)
    name = char.name if char else (avatar.get("name") or "")
    elem = elem_name(avatar.get("elem") or (char.get("elem") if char else ""), game)
    lines = [f"{name} Lv{avatar.get('level')} C{avatar.get('cons') or 0} {elem}"]
    if game == "gs":
        lines.append(f"生命 {_fmt_base_plus(attr, 'hp')} 攻击 {_fmt_base_plus(attr, 'atk')}")
        lines.append(f"防御 {_fmt_base_plus(attr, 'def')} 精通 {round(attr.get('mastery') or 0)}")
        lines.append(
            f"暴击 {(attr.get('cpct') or 0):.1f}% 暴伤 {(attr.get('cdmg') or 0):.1f}% "
            f"充能 {(attr.get('recharge') or 0):.1f}%"
        )
        lines.append(
            f"元素伤害 {(attr.get('dmg') or 0):.1f}% 物理伤害 {(attr.get('phy') or 0):.1f}% "
            f"治疗加成 {(attr.get('heal') or 0):.1f}%"
        )
    else:
        lines.append(f"生命 {_fmt_base_plus(attr, 'hp')} 攻击 {_fmt_base_plus(attr, 'atk')}")
        lines.append(f"防御 {_fmt_base_plus(attr, 'def')} 速度 {round(attr.get('speed') or 0)}")
        lines.append(
            f"暴击 {(attr.get('cpct') or 0):.1f}% 暴伤 {(attr.get('cdmg') or 0):.1f}% "
            f"充能 {(attr.get('recharge') or 0):.1f}%"
        )
        lines.append(
            f"伤害加成 {(attr.get('dmg') or 0):.1f}% 击破特攻 {(attr.get('stance') or 0):.1f}% "
            f"治疗加成 {(attr.get('heal') or 0):.1f}%"
        )
    w = avatar.get("weapon") or {}
    if w.get("name"):
        w_affix_label = "精" if game == "gs" else "叠"
        lines.append(f"武器：{w['name']} Lv{w.get('level') or 1} {w_affix_label}{w.get('affix') or 1}")
    mark_class = mark.get("markClass") or "-"
    lines.append(f"圣遗物评分：{mark['mark']:.1f}（{mark_class}）[{mark['classTitle']}]")
    for idx in sorted(mark["artis"]):
        piece = mark["artis"][idx]
        pos = _POS_NAME[game][idx - 1]
        main = piece.get("main") or {}
        main_text = ""
        if main.get("key"):
            main_text = f"{key_title(main['key'], game)}{format_value(main['key'], main.get('value') or 0, game)}"
        piece_class = piece.get("markClass") or "-"
        lines.append(f"{pos} {piece['name']} +{piece['level']} {main_text} {piece['mark']:.1f}({piece_class})")
    return "\n".join(lines)


def _fmt_dmg_text(dmg: dict[str, Any]) -> str:
    """伤害模式的临时文本输出；P8 卡片渲染会直接消费同一结构。"""
    selected = dmg["selected"]
    lines = [f"{dmg['character']} 伤害计算（默认敌人 Lv103）："]
    for idx, row in enumerate(dmg["dmgData"], 1):
        lines.append(f"{idx}. {dmg_row_text(row)}")
    if dmg.get("dmgMsg"):
        lines.append("Buff：" + "；".join(filter(None, dmg["dmgMsg"])))
    lines.append(f"当前：{dmg_row_text(selected)}")
    return "\n".join(lines)


@detail_m.handle()
@guard(detail_m)
async def _(event: MessageEvent):
    target = _mentioned_panel_user(event)
    await ensure_panel_available(detail_m, event, feature="detail")
    text = event.get_plaintext().strip()
    m = re.match(RE_DETAIL, text)
    if not m:
        await detail_m.finish()
    game = game_of(text)
    name = re.sub(r"^(星铁|原神)", "", m.group(1)).strip()
    char = meta.resolve_character(name, game)
    if not char:
        await detail_m.finish(_char_not_found(name, game))
    uid = _panel_uid(event, text, game, target)
    player = Player.load(uid, game)
    avatar = player.get_avatar(name)
    if not avatar:
        await detail_m.finish(f"本地没有 {char.name} 的面板数据，{_update_tip(game, target)}")
    detail_type = m.group(2)
    show_dmg = detail_type.startswith("伤害")
    tail = detail_type[2:] if show_dmg else ""
    dmg_idx = int(tail) if tail else None

    check = await authorize_panel_call(event)
    if check.needs_confirm:
        register_pending(
            event,
            feature="detail",
            payload={
                "avatar": avatar,
                "game": game,
                "uid": player.uid,
                "show_dmg": show_dmg,
                "dmg_idx": dmg_idx,
            },
            cost=check.cost,
        )
        await detail_m.finish(check.message)
    if not check.allowed:
        await detail_m.finish(check.message)
    if isinstance(event, GroupMessageEvent):
        clear_pending(event.group_id, event.get_user_id())
    await _send_detail_card(detail_m, avatar, game, player.uid, show_dmg, dmg_idx)
    await detail_m.finish()


async def _send_detail_card(matcher, avatar: dict, game: str, uid: str, show_dmg: bool, dmg_idx: int | None) -> None:
    """计算属性/评分/伤害并发送详情卡。"""
    attr = calc_attr(avatar, game)
    dmg = calc_dmg(avatar, game, dmg_idx) if show_dmg else None
    mark = calc_mark(avatar, attr, game)
    await send_image(matcher, await render_profile_detail(avatar, attr, mark, game, dmg, uid=uid))


@confirm_m.handle()
@guard(confirm_m)
async def _(event: MessageEvent, bot: Bot):
    # 待确认请求可能是在非开放时段登记的，确认时不再重复时段检查
    await ensure_panel_enabled(confirm_m)
    # guard 的 functools.wraps 会让 NoneBot 用 common.py 的 globals 解析注解，
    # 这里统一用 MessageEvent，运行时再确认是群消息。
    if not isinstance(event, GroupMessageEvent):
        await confirm_m.finish()
    group_id = str(event.group_id)
    timeout = confirm_timeout()
    pending = pop_pending(group_id, event.get_user_id(), timeout)
    if pending is None:
        await confirm_m.finish("没有有效的待确认面板请求，请重新发送面板指令。")
    if event.get_plaintext().strip() in ("取消", "/面板取消"):
        await confirm_m.finish("已取消本次查看。")
    check = await authorize_panel_call(
        event, confirmed=True, feature=pending.feature, quoted_cost=pending.cost
    )
    if not check.allowed:
        await confirm_m.finish(check.message)
    if pending.feature == "rank":
        await _send_rank_card(confirm_m, bot, event, pending.payload)
    else:
        payload = pending.payload
        await _send_detail_card(
            confirm_m,
            payload.get("avatar") or {},
            str(payload.get("game") or "gs"),
            str(payload.get("uid") or ""),
            bool(payload.get("show_dmg")),
            payload.get("dmg_idx"),
        )
    await confirm_m.finish()


# ---------------------------------------------------------------------------
# /面板排名（群面板排名 / 角色名面板排名 / 角色名伤害排名）
# ---------------------------------------------------------------------------


def _parse_rank_body(text: str) -> tuple[str, str]:
    """从「/优菈伤害排名」里解析 (角色名, 模式)；空角色名表示群友综合榜。"""
    m = re.match(RE_RANK, text)
    body = (m.group(2) if m else "").strip()
    mode = MODE_DMG if "伤害" in body else MODE_MARK
    body = re.sub(r"(面板|角色|评分|伤害|双爆|词条|圣遗物|遗器)$", "", body).strip()
    body = re.sub(r"^(群内|群友|群|本群)", "", body).strip()
    body = re.sub(r"^(面板|角色)", "", body).strip()
    return body, mode


async def _send_rank_card(matcher, bot: Bot, event: MessageEvent, payload: dict[str, Any]) -> None:
    """收集群成员面板并发送排名卡。"""
    game = str(payload.get("game") or "gs")
    char_name = payload.get("char") or None
    mode = str(payload.get("mode") or MODE_MARK)
    entries = await collect_rank(bot, event.group_id, game, char_name, mode)
    await send_image(
        matcher,
        await render_profile_rank(
            entries,
            game,
            char_name=char_name,
            mode=mode,
            group_label=f"群 {event.group_id}",
            member_count=len(entries),
        ),
    )


@rank_m.handle()
@guard(rank_m)
async def _(event: MessageEvent, bot: Bot):
    text = event.get_plaintext()
    m = re.match(RE_RANK, text)
    if not m:
        await rank_m.finish()
    if not isinstance(event, GroupMessageEvent):
        await rank_m.finish("面板排名仅支持群聊。")
    await ensure_panel_available(rank_m, event, feature="rank")
    game = game_of(text)
    char_name, mode = _parse_rank_body(text)
    char = meta.resolve_character(char_name, game) if char_name else None
    if char_name and not char:
        await rank_m.finish(_char_not_found(char_name, game))
    if char is not None:
        char_name = char.name
    else:
        # 综合榜（每人取最高分角色）只按评分排序
        char_name, mode = None, MODE_MARK

    check = await authorize_panel_call(event, feature="rank")
    if check.needs_confirm:
        register_pending(
            event,
            feature="rank",
            payload={"game": game, "char": char_name, "mode": mode},
            cost=check.cost,
        )
        await rank_m.finish(check.message)
    if not check.allowed:
        await rank_m.finish(check.message)
    clear_pending(event.group_id, event.get_user_id())
    await _send_rank_card(rank_m, bot, event, {"game": game, "char": char_name, "mode": mode})
    await rank_m.finish()


# ---------------------------------------------------------------------------
# /圣遗物列表
# ---------------------------------------------------------------------------


@artis_list_m.handle()
@guard(artis_list_m)
async def _(event: MessageEvent):
    await ensure_panel_available(artis_list_m, event)
    text = event.get_plaintext()
    game = game_of(text)
    uid = resolve_uid(event, text, game)
    if not uid:
        await artis_list_m.finish(_bind_tip(game))
    player = Player.load(uid, game)
    if not player.avatars:
        await artis_list_m.finish(_update_tip(game))
    await send_image(artis_list_m, await render_artis_list(player, game))
    await artis_list_m.finish()
