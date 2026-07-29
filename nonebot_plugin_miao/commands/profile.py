"""面板指令（纯文本版，面板图渲染后续做）：
#更新面板 / #面板列表 / #角色名面板（含圣遗物评分）/ #圣遗物列表

面板属性计算见 core/attr_calc.py，评分见 core/artis_mark.py。
"""
from __future__ import annotations

import re
from typing import Any

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

from ..core import meta
from ..core.artis_mark import calc_mark, format_value, key_title
from ..core.attr_calc import calc_attr, elem_name
from ..core.player import Player
from ..datasource.errors import ProfileError
from ..datasource.mys import update_profile_mys
from ..datasource.profile_service import update_profile
from ..dmg import calc_dmg
from ..render import render_artis_list, render_profile_detail, render_profile_list
from .common import game_of, guard, resolve_uid, send_image

_GAME_NAME = {"gs": "原神", "sr": "星铁"}
# 部位简称（花羽沙杯冠 / 头手衣鞋球绳）
_POS_NAME = {"gs": ["花", "羽", "沙", "杯", "冠"], "sr": ["头", "手", "衣", "鞋", "球", "绳"]}

# 正则常量（导出以便测试交叉命中）
RE_UPDATE = r"^#(星铁|原神)?(全部面板更新|更新全部面板|获取游戏角色详情|更新面板|面板更新)\s*(\d{9,10})?$"
RE_MYS_UPDATE = r"^#(星铁|原神)?(米游社|mys)(更新面板|面板更新)\s*(\d{9,10})?$"
RE_LIST = r"^#(星铁|原神)?(面板角色|角色面板|面板)(列表)?\s*(\d{9,10})?$"
RE_ARTIS_LIST = r"^#(星铁|原神)?(圣遗物|遗器)列表\s*(\d{9,10})?$"
# 角色名面板：负向前瞻排除 更新面板/面板列表/圣遗物列表/米游社更新面板 等指令（另有优先级兜底）
RE_DETAIL = (
    r"^#*(?!(?:星铁|原神)?(?:(?:米游社|mys)(?:更新面板|面板更新)|全部面板更新|更新全部面板|获取游戏角色详情|"
    r"更新面板|面板更新|面板角色|角色面板|面板|面板列表|圣遗物列表|遗器列表)\s*(?:\d{9,10})?$)"
    r"([^#]+?)\s*(详细|详情|面板|面版|圣遗物|遗器|伤害(?:[1-9]+\d*)?)\s*(\d{9,10})?$"
)

update_m = on_regex(RE_UPDATE, priority=5, block=True)
mys_update_m = on_regex(RE_MYS_UPDATE, priority=5, block=True)
list_m = on_regex(RE_LIST, priority=5, block=True)
artis_list_m = on_regex(RE_ARTIS_LIST, priority=5, block=True)
# 角色名面板为正则兜底，优先级低于上面三条，避免吃掉 #更新面板/#面板列表/#圣遗物列表
detail_m = on_regex(RE_DETAIL, priority=10, block=True)


def _bind_tip(game: str) -> str:
    return f"请先绑定{_GAME_NAME[game]} UID：#{'星铁' if game == 'sr' else ''}绑定uid <UID>"


def _update_tip(game: str) -> str:
    return f"暂无本地面板数据，请先发送 #{'星铁' if game == 'sr' else ''}更新面板"


def _avatar_line(avatar: dict[str, Any], fallback: str = "") -> str:
    return f"{avatar.get('name') or fallback} Lv{avatar.get('level') or '?'} C{avatar.get('cons') or 0}"


# ---------------------------------------------------------------------------
# #更新面板
# ---------------------------------------------------------------------------


@update_m.handle()
@guard(update_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    uid = resolve_uid(event, text, game)
    if not uid:
        await update_m.finish(_bind_tip(game))
    ret = await update_profile(event.get_user_id(), uid, game)
    if ret["code"] == "cd":
        await update_m.finish(f"面板数据更新冷却中，请 {ret['wait']} 秒后再试")
    player: Player = ret["player"]
    lines = [f"{_GAME_NAME[game]} UID {uid} 面板更新成功，本次更新 {len(ret['new_chars'])} 个角色："]
    for name in ret["new_chars"]:
        lines.append(_avatar_line(player.get_avatar(name) or {}, name))
    await update_m.finish("\n".join(lines))


# ---------------------------------------------------------------------------
# #米游社更新面板
# ---------------------------------------------------------------------------


@mys_update_m.handle()
@guard(mys_update_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    uid = resolve_uid(event, text, game)
    if not uid:
        await mys_update_m.finish(_bind_tip(game))
    try:
        ret = await update_profile_mys(event.get_user_id(), uid, game)
    except ProfileError as e:
        await mys_update_m.finish(str(e))
    if ret["code"] == "no_cookie":
        await mys_update_m.finish(ret["msg"])
    if ret["code"] == "cd":
        await mys_update_m.finish(f"面板数据更新冷却中，请 {ret['wait']} 秒后再试")
    player: Player = ret["player"]
    lines = [f"{_GAME_NAME[game]} UID {uid} 米游社面板更新成功，本次更新 {len(ret['new_chars'])} 个角色："]
    for name in ret["new_chars"]:
        lines.append(_avatar_line(player.get_avatar(name) or {}, name))
    await mys_update_m.finish("\n".join(lines))


# ---------------------------------------------------------------------------
# #面板列表
# ---------------------------------------------------------------------------


@list_m.handle()
@guard(list_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    uid = resolve_uid(event, text, game)
    if not uid:
        await list_m.finish(_bind_tip(game))
    player = Player.load(uid, game)
    if not player.avatars:
        await list_m.finish(_update_tip(game))
    await send_image(list_m, await render_profile_list(player, game))
    await list_m.finish()


# ---------------------------------------------------------------------------
# #角色名面板（详细/详情/面板/面版/圣遗物/遗器）
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
        lines.append(f"{idx}. {row['title']}：期望 {row['avg']:.0f} / 暴击 {row['dmg']:.0f}")
    if dmg.get("dmgMsg"):
        lines.append("Buff：" + "；".join(filter(None, dmg["dmgMsg"])))
    lines.append(f"当前：{selected['title']}，期望 {selected['avg']:.0f} / 暴击 {selected['dmg']:.0f}")
    return "\n".join(lines)


@detail_m.handle()
@guard(detail_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    m = re.match(RE_DETAIL, text)
    if not m:
        await detail_m.finish()
    game = game_of(text)
    name = re.sub(r"^(星铁|原神)", "", m.group(1)).strip()
    char = meta.get_character(name, game)
    if not char:
        await detail_m.finish(f"未找到角色【{name}】，请检查角色名")
    uid = resolve_uid(event, text, game)
    if not uid:
        await detail_m.finish(_bind_tip(game))
    player = Player.load(uid, game)
    avatar = player.get_avatar(name)
    if not avatar:
        await detail_m.finish(f"本地没有 {char.name} 的面板数据，{_update_tip(game)}")
    attr = calc_attr(avatar, game)
    detail_type = m.group(2)
    if detail_type.startswith("伤害"):
        tail = detail_type[2:]
        dmg = calc_dmg(avatar, game, int(tail) if tail else None)
    mark = calc_mark(avatar, attr, game)
    dmg_data = dmg if detail_type.startswith("伤害") else None
    await send_image(detail_m, await render_profile_detail(avatar, attr, mark, game, dmg_data))
    await detail_m.finish()


# ---------------------------------------------------------------------------
# #圣遗物列表
# ---------------------------------------------------------------------------


@artis_list_m.handle()
@guard(artis_list_m)
async def _(event: MessageEvent):
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
