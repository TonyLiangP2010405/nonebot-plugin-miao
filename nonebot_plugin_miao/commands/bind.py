"""绑定相关指令：#绑定uid / #删除绑定 / #绑定cookie / #删除cookie / #我的绑定

全部走 on_regex（默认 command_start 不含 "#"，on_command 无法命中 "#绑定uid" 这类指令）。
"""
from __future__ import annotations

import re

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.event import Sender

from ..core import store
from .common import game_of, guard

_GAME_NAME = {"gs": "原神", "sr": "星铁"}

# 正则常量（导出以便测试交叉命中）
RE_BIND_UID = r"^#(星铁)?绑定([uU][iI][dD])?\s*(\d{0,11})\s*$"
RE_DEL_BIND = r"^#删除(星铁)?绑定$"
RE_BIND_COOKIE = r"^#绑定cookie\s*([\s\S]*)$"
RE_DEL_COOKIE = r"^#删除cookie$"
RE_MY_BIND = r"^#我的绑定$"

bind_uid_m = on_regex(RE_BIND_UID, priority=5, block=True)
del_bind_m = on_regex(RE_DEL_BIND, priority=5, block=True)
bind_cookie_m = on_regex(RE_BIND_COOKIE, priority=5, block=True)
del_cookie_m = on_regex(RE_DEL_COOKIE, priority=5, block=True)
my_bind_m = on_regex(RE_MY_BIND, priority=5, block=True)


def _nickname(sender: Sender) -> str:
    return sender.card or sender.nickname or ""


@bind_uid_m.handle()
@guard(bind_uid_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    game_name = _GAME_NAME[game]
    uid = resolve_uid_in_bind(text)
    if uid:
        # 非法 UID 时 store.bind_uid 抛 ValueError，由 guard 统一回提示
        uid = store.bind_uid(event.get_user_id(), game, uid)
        await bind_uid_m.finish(f"绑定成功：{game_name} UID {uid}")
    old = store.get_uid(event.get_user_id(), game)
    if old:
        await bind_uid_m.finish(
            f"你已绑定{game_name} UID {old}\n如需更换，请发送 #{'星铁' if game == 'sr' else ''}绑定uid <新UID>"
        )
    await bind_uid_m.finish(f"请发送 #{'星铁' if game == 'sr' else ''}绑定uid <你的{game_name}UID>")


def resolve_uid_in_bind(text: str) -> str | None:
    """从绑定指令文本中提取紧跟的数字参数（不回头找绑定记录）"""
    m = re.search(r"(\d+)\s*$", text)
    return m.group(1) if m else None


@del_bind_m.handle()
@guard(del_bind_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    if store.del_bind(event.get_user_id(), game):
        await del_bind_m.finish(f"已删除{_GAME_NAME[game]} UID 绑定")
    await del_bind_m.finish(f"你还没有绑定{_GAME_NAME[game]} UID")


@bind_cookie_m.handle()
@guard(bind_cookie_m)
async def _(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        await bind_cookie_m.finish("为保护账号安全，cookie 请私聊发送给我（#绑定cookie <cookie>）")
    text = event.get_plaintext()
    cookie = text.split("绑定cookie", 1)[1].strip()
    if not cookie:
        await bind_cookie_m.finish("请在指令后跟上 cookie：#绑定cookie <你的米游社cookie>")
    store.set_cookie(event.get_user_id(), cookie)
    await bind_cookie_m.finish("cookie 已保存，仅存储在 bot 本地\n如需删除请发送 #删除cookie")


@del_cookie_m.handle()
@guard(del_cookie_m)
async def _(event: MessageEvent):
    if store.del_cookie(event.get_user_id()):
        await del_cookie_m.finish("已删除你的 cookie")
    await del_cookie_m.finish("你还没有绑定 cookie")


@my_bind_m.handle()
@guard(my_bind_m)
async def _(event: MessageEvent):
    user_id = event.get_user_id()
    gs_uid = store.get_uid(user_id, "gs")
    sr_uid = store.get_uid(user_id, "sr")
    has_cookie = bool(store.get_cookie(user_id))
    name = _nickname(event.sender) or user_id
    lines = [f"{name} 的绑定信息："]
    lines.append(f"原神 UID：{gs_uid or '未绑定'}")
    lines.append(f"星铁 UID：{sr_uid or '未绑定'}")
    lines.append(f"米游社 cookie：{'已绑定' if has_cookie else '未绑定'}")
    await my_bind_m.finish("\n".join(lines))
