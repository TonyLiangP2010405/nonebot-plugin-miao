"""原神角色与武器图鉴指令。"""
from __future__ import annotations

import re

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

from ..core import meta
from ..render import render_character_encyclopedia, render_encyclopedia_index, render_weapon_encyclopedia
from .common import guard, send_image

# 兼容本插件的 / 前缀与 Yunzai 用户习惯的 # 前缀：
# /芙宁娜图鉴、#雾切图鉴、/图鉴 芙宁娜、/角色索引、/武器列表。
RE_ENCYCLOPEDIA = r"^[/#](?:(?:原神)?(.+?)图鉴|图鉴(?:\s+(.+?))?|(?:原神)?(角色|武器)(?:索引|列表))\s*$"

encyclopedia_m = on_regex(RE_ENCYCLOPEDIA, priority=6, block=True)


def parse_encyclopedia_query(text: str) -> str:
    """提取图鉴查询词，未提供名称时返回空字符串。"""
    match = re.match(RE_ENCYCLOPEDIA, str(text or ""))
    if not match:
        return ""
    return next((part.strip() for part in match.groups() if part and part.strip()), "")


def find_encyclopedia_entry(query: str):
    """按角色优先、武器其次查找图鉴条目。"""
    character = meta.get_character(query, "gs")
    if character:
        return "character", character
    weapon = meta.get_weapon(query, "gs")
    if weapon:
        return "weapon", weapon
    return None


@encyclopedia_m.handle()
@guard(encyclopedia_m)
async def _(event: MessageEvent):
    query = parse_encyclopedia_query(event.get_plaintext())
    if not query or query in {"帮助", "help"}:
        await encyclopedia_m.finish(
            "图鉴指令：\n"
            "/角色图鉴　查看全部角色\n"
            "/武器图鉴　查看全部武器\n"
            "/芙宁娜图鉴　查看角色资料\n"
            "/雾切图鉴　查看武器资料\n"
            "以上指令也支持 # 前缀"
        )
    if query == "角色":
        await send_image(encyclopedia_m, await render_encyclopedia_index("character"))
        await encyclopedia_m.finish()
    if query == "武器":
        await send_image(encyclopedia_m, await render_encyclopedia_index("weapon"))
        await encyclopedia_m.finish()

    entry = find_encyclopedia_entry(query)
    if entry and entry[0] == "character":
        await send_image(encyclopedia_m, await render_character_encyclopedia(entry[1]))
        await encyclopedia_m.finish()
    if entry and entry[0] == "weapon":
        await send_image(encyclopedia_m, await render_weapon_encyclopedia(entry[1]))
        await encyclopedia_m.finish()
    await encyclopedia_m.finish(f"未找到原神角色或武器【{query}】，可发送 /角色图鉴 或 /武器图鉴 查看索引")
