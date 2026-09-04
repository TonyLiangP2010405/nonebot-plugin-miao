"""原神、星铁与绝区零角色攻略图指令。"""
from __future__ import annotations

import re

from nonebot import get_plugin_config, on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

from ..config import Config
from ..core import meta, store
from ..datasource.strategy import (
    GAME_NAMES,
    SOURCE_NAMES_BY_GAME,
    fetch_strategy_image,
    source_count,
    source_name,
)
from .common import guard, send_image

_GAME_PATTERN = r"(原神|崩坏星穹铁道|星穹铁道|星铁|崩铁|铁道|绝区零|绝区|ZZZ|zzz)"
_GAME_BY_PREFIX = {
    None: "gs",
    "原神": "gs",
    "星铁": "sr",
    "崩铁": "sr",
    "铁道": "sr",
    "星穹铁道": "sr",
    "崩坏星穹铁道": "sr",
    "绝区零": "zzz",
    "绝区": "zzz",
    "ZZZ": "zzz",
    "zzz": "zzz",
}
_COMMAND_PREFIX = {"gs": "", "sr": "星铁", "zzz": "绝区零"}
_CONFIG_ATTR = {
    "gs": "miao_strategy_default_source",
    "sr": "miao_sr_strategy_default_source",
    "zzz": "miao_zzz_strategy_default_source",
}

# 保留 Yunzai 的 # 指令，同时兼容本插件的 / 前缀：
# /心海攻略4、/星铁流萤攻略2、/绝区零星见雅攻略、/更新星铁流萤攻略。
RE_STRATEGY = rf"^[/#](更新)?(?:{_GAME_PATTERN})?([^\s/#]+?)攻略([0-9]+)?\s*$"
RE_STRATEGY_HELP = rf"^[/#](?:{_GAME_PATTERN})?攻略(?:说明|帮助)?\s*$"
RE_STRATEGY_SETTING = rf"^[/#]设置(?:{_GAME_PATTERN})?默认攻略([0-9]+)?\s*$"

strategy_help_m = on_regex(RE_STRATEGY_HELP, priority=5, block=True)
strategy_setting_m = on_regex(RE_STRATEGY_SETTING, priority=5, block=True)
strategy_m = on_regex(RE_STRATEGY, priority=6, block=True)


def _game_of_prefix(prefix: str | None) -> str:
    return _GAME_BY_PREFIX.get(prefix, "gs")


def parse_strategy_query(text: str) -> tuple[bool, str, str, int | None] | None:
    """解析攻略查询，返回（是否强制刷新、游戏、角色名、来源编号）。"""
    match = re.match(RE_STRATEGY, str(text or ""))
    if not match:
        return None
    source = int(match.group(4)) if match.group(4) else None
    return bool(match.group(1)), _game_of_prefix(match.group(2)), match.group(3).strip(), source


def parse_strategy_help_game(text: str) -> str | None:
    """解析帮助指令指定的游戏；未指定时返回 None，表示展示全部。"""
    match = re.match(RE_STRATEGY_HELP, str(text or ""))
    if not match:
        return None
    return _game_of_prefix(match.group(1)) if match.group(1) else None


def parse_strategy_setting(text: str) -> tuple[str, int | None] | None:
    """解析默认来源设置指令。"""
    match = re.match(RE_STRATEGY_SETTING, str(text or ""))
    if not match:
        return None
    source = int(match.group(2)) if match.group(2) else None
    return _game_of_prefix(match.group(1)), source


def _configured_default_source(game: str) -> int:
    try:
        config = get_plugin_config(Config)
        source = int(getattr(config, _CONFIG_ATTR[game]))
    except (AttributeError, TypeError, ValueError):
        source = 1
    return source if 1 <= source <= source_count(game) else 1


def default_source(game: str = "gs") -> int:
    """返回指定游戏持久化设置或环境配置中的默认攻略来源。"""
    return store.get_strategy_default_source(_configured_default_source(game), game)


def strategy_help_text(game: str | None = None) -> str:
    lines = [
        "攻略帮助：",
        "/心海攻略[1-7]　查询原神角色攻略",
        "/星铁流萤攻略[1-3]　查询星铁角色攻略",
        "/绝区零星见雅攻略[1-4]　查询绝区零角色攻略",
        "/更新<上述指令>　忽略缓存并重新获取",
        "/设置默认攻略2　设置原神默认来源",
        "/设置星铁默认攻略2 / /设置绝区零默认攻略2",
        "以上指令兼容 Yunzai 常用的 # 前缀",
    ]
    games = (game,) if game else ("gs", "sr", "zzz")
    for item in games:
        lines.append(f"\n{GAME_NAMES[item]}攻略来源：")
        lines.extend(f"{index}——{name}" for index, name in enumerate(SOURCE_NAMES_BY_GAME[item], 1))
    return "\n".join(lines)


def _resolve_role_name(role_query: str, game: str) -> str | None:
    if game == "zzz":
        # 插件没有绝区零本地元数据；直接按正式角色名匹配，使新角色无需等待资源更新。
        return role_query.strip() or None
    role = meta.get_character(role_query, game)
    return role.name if role else None


@strategy_help_m.handle()
@guard(strategy_help_m)
async def _strategy_help(event: MessageEvent) -> None:
    await strategy_help_m.finish(strategy_help_text(parse_strategy_help_game(event.get_plaintext())))


@strategy_setting_m.handle()
@guard(strategy_setting_m)
async def _strategy_setting(event: MessageEvent) -> None:
    parsed = parse_strategy_setting(event.get_plaintext())
    if not parsed or parsed[1] is None:
        await strategy_setting_m.finish(
            "默认攻略设置方式：/设置默认攻略2、/设置星铁默认攻略2、/设置绝区零默认攻略2"
        )
    game, source = parsed
    value = store.set_strategy_default_source(source, game)
    await strategy_setting_m.finish(f"{GAME_NAMES[game]}默认攻略来源已设置为：{value}——{source_name(value, game)}")


@strategy_m.handle()
@guard(strategy_m)
async def _strategy(event: MessageEvent) -> None:
    parsed = parse_strategy_query(event.get_plaintext())
    if not parsed:
        await strategy_m.finish("攻略指令格式错误，可发送 /攻略帮助 查看说明")

    refresh, game, role_query, selected_source = parsed
    limit = source_count(game)
    if selected_source is not None and not 1 <= selected_source <= limit:
        await strategy_m.finish(f"{GAME_NAMES[game]}攻略来源必须是 1-{limit} 的数字，可发送 /攻略帮助 查看来源")

    role_name = _resolve_role_name(role_query, game)
    if not role_name:
        hint = "，可发送 /角色图鉴 查看角色索引" if game == "gs" else "，请检查角色名或常用别名"
        await strategy_m.finish(f"未找到{GAME_NAMES[game]}角色【{role_query}】{hint}")

    source = selected_source or default_source(game)
    image = await fetch_strategy_image(role_name, source, game=game, refresh=refresh)
    if not image:
        prefix = _COMMAND_PREFIX[game]
        await strategy_m.finish(
            f"暂无{role_name}攻略（{source_name(source, game)}）\n"
            f"请尝试其他攻略来源，例如 /{prefix}{role_name}攻略2；发送 /攻略帮助 查看说明"
        )
    await send_image(strategy_m, image)
    await strategy_m.finish()
