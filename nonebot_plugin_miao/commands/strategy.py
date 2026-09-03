"""原神角色攻略图指令。"""
from __future__ import annotations

import re

from nonebot import get_plugin_config, on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

from ..config import Config
from ..core import meta, store
from ..datasource.strategy import SOURCE_NAMES, fetch_strategy_image, source_name
from .common import guard, send_image

# 保留 Yunzai 的 # 指令，同时兼容本插件的 / 前缀：
# #心海攻略4、/更新早柚攻略、/攻略帮助、/设置默认攻略2。
RE_STRATEGY = r"^[/#](更新)?(?!(?:星铁|设置默认))([^\s/#]+?)攻略([0-9]+)?\s*$"
RE_STRATEGY_HELP = r"^[/#]攻略(?:说明|帮助)?\s*$"
RE_STRATEGY_SETTING = r"^[/#]设置默认攻略([0-9]+)?\s*$"

strategy_help_m = on_regex(RE_STRATEGY_HELP, priority=5, block=True)
strategy_setting_m = on_regex(RE_STRATEGY_SETTING, priority=5, block=True)
strategy_m = on_regex(RE_STRATEGY, priority=6, block=True)


def parse_strategy_query(text: str) -> tuple[bool, str, int | None] | None:
    """解析攻略查询，返回（是否强制刷新、角色名、来源编号）。"""
    match = re.match(RE_STRATEGY, str(text or ""))
    if not match:
        return None
    source = int(match.group(3)) if match.group(3) else None
    return bool(match.group(1)), match.group(2).strip(), source


def _configured_default_source() -> int:
    try:
        source = int(get_plugin_config(Config).miao_strategy_default_source)
    except (TypeError, ValueError):
        source = 1
    return source if 1 <= source <= 7 else 1


def default_source() -> int:
    """返回持久化设置或环境配置中的默认攻略来源。"""
    return store.get_strategy_default_source(_configured_default_source())


def strategy_help_text() -> str:
    sources = "\n".join(f"{index}——{name}" for index, name in enumerate(SOURCE_NAMES, 1))
    return (
        "攻略帮助：\n"
        "/心海攻略[1-7]　查询角色攻略图\n"
        "/更新早柚攻略[1-7]　强制刷新攻略图\n"
        "/设置默认攻略[1-7]　设置默认来源\n"
        "以上指令兼容 Yunzai 常用的 # 前缀\n\n"
        f"攻略来源：\n{sources}"
    )


@strategy_help_m.handle()
@guard(strategy_help_m)
async def _strategy_help() -> None:
    await strategy_help_m.finish(strategy_help_text())


@strategy_setting_m.handle()
@guard(strategy_setting_m)
async def _strategy_setting(event: MessageEvent) -> None:
    match = re.match(RE_STRATEGY_SETTING, event.get_plaintext())
    if not match or not match.group(1):
        await strategy_setting_m.finish("默认攻略设置方式：/设置默认攻略[1-7]\n例如：/设置默认攻略2")
    source = store.set_strategy_default_source(int(match.group(1)))
    await strategy_setting_m.finish(f"默认攻略来源已设置为：{source}——{source_name(source)}")


@strategy_m.handle()
@guard(strategy_m)
async def _strategy(event: MessageEvent) -> None:
    parsed = parse_strategy_query(event.get_plaintext())
    if not parsed:
        await strategy_m.finish("攻略指令格式错误，可发送 /攻略帮助 查看说明")

    refresh, role_query, selected_source = parsed
    if selected_source is not None and not 1 <= selected_source <= 7:
        await strategy_m.finish("攻略来源必须是 1-7 的数字，可发送 /攻略帮助 查看来源")

    role = meta.get_character(role_query, "gs")
    if not role:
        await strategy_m.finish(f"未找到原神角色【{role_query}】，可发送 /角色图鉴 查看角色索引")

    source = selected_source or default_source()
    image = await fetch_strategy_image(role.name, source, refresh=refresh)
    if not image:
        await strategy_m.finish(
            f"暂无{role.name}攻略（{source_name(source)}）\n"
            "请尝试其他攻略来源，例如 /角色名攻略2；发送 /攻略帮助 查看说明"
        )
    await send_image(strategy_m, image)
    await strategy_m.finish()
