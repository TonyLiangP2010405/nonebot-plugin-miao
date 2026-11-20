"""面板开放 / 关闭的群提醒。

开放与关闭时刻各发一轮提醒，默认连发 3 遍（``MIAO_PANEL_NOTIFY_REPEAT``）。
只发给用过面板指令的群（``store.panel_groups()``），避免骚扰无关群。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from nonebot import get_bots, logger

from . import store
from .panel_quota import panel_enabled, panel_window, panel_window_text

try:  # Windows 未安装 tzdata 时退回固定 UTC+8
    from zoneinfo import ZoneInfo

    _PANEL_TZ: Any = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - 取决于运行环境
    _PANEL_TZ = timezone(timedelta(hours=8))


def _plugin_config() -> Any:
    try:
        from nonebot import get_plugin_config

        from ..config import Config

        return get_plugin_config(Config)
    except Exception:
        from ..config import Config

        return Config()


def _int_attr(config: Any, name: str, default: int) -> int:
    try:
        return int(getattr(config, name, default) or 0)
    except (TypeError, ValueError):
        return default


def panel_notify_text(kind: str, config: Any | None = None) -> str:
    """生成开启 / 关闭提醒文案，明确写出非开放时段的 50 好感规则。"""
    config = config or _plugin_config()
    window = panel_window_text(config)
    free = _int_attr(config, "miao_panel_free_per_hour", 1)
    limit = _int_attr(config, "miao_panel_user_hourly_limit", 2)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)
    offhour = _int_attr(config, "miao_panel_offhour_affection_cost", 50)
    open_hour, _ = panel_window(config)

    if kind == "open":
        quota = f"免费 {free} 次" if free > 0 else "免费次数已用完"
        if limit > 0:
            quota += f"、每小时最多 {limit} 次"
        pay = f"，之后每次 {cost} 点好感度" if cost > 0 else ""
        offhour_line = (
            f"非开放时段每次 {offhour} 点好感度，回复「确认」后扣费。"
            if offhour > 0
            else "非开放时段暂不可查看。"
        )
        return (
            f"【面板提醒】面板功能已开启（{window}）。\n"
            f"角色面板 / 群排名各自{quota}{pay}，需回复「确认」。\n"
            f"转盘次数优先且不占额度；{offhour_line}"
        )

    if offhour > 0:
        closed_line = (
            f"非开放时段仍可查看：转盘次数优先，否则每次 {offhour} 点好感度，"
            "回复「确认」后扣费。"
        )
    else:
        closed_line = f"非开放时段暂不可查看，{open_hour:02d}:00 后再试。"
    return (
        f"【面板提醒】面板功能已关闭（{window} 外）。\n"
        f"{closed_line}\n"
        "发送 /面板次数 可查看剩余次数。"
    )


def _pick_bot() -> Any | None:
    """取一个支持群消息的已连接 Bot。"""
    for bot in get_bots().values():
        if hasattr(bot, "send_group_msg"):
            return bot
    return None


async def notify_panel_window(
    kind: str,
    config: Any | None = None,
    *,
    repeat: int | None = None,
) -> int:
    """向面板群发送开启 / 关闭提醒，返回成功发送条数。"""
    config = config or _plugin_config()
    if not bool(getattr(config, "miao_panel_notify_enabled", True)):
        return 0
    if not panel_enabled(config):
        return 0
    count = repeat if repeat is not None else _int_attr(config, "miao_panel_notify_repeat", 3)
    count = min(5, max(1, count))
    groups = store.panel_groups()
    if not groups:
        logger.info("[miao] 没有用过面板的群，跳过开放/关闭提醒")
        return 0
    bot = _pick_bot()
    if bot is None:
        logger.warning("[miao] 没有已连接的 Bot，跳过开放/关闭提醒")
        return 0
    text = panel_notify_text(kind, config)
    sent = 0
    for group_id in groups:
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            continue
        for index in range(count):
            try:
                await bot.send_group_msg(group_id=gid, message=text)
                sent += 1
            except Exception as e:
                logger.warning(f"[miao] 面板提醒发送失败（群 {gid}）: {e}")
                break
            if index + 1 < count:
                await asyncio.sleep(1)
    return sent


async def panel_window_tick(now: datetime | None = None) -> int:
    """定时任务入口：正好在开启 / 关闭整点时发提醒。"""
    config = _plugin_config()
    if not bool(getattr(config, "miao_panel_notify_enabled", True)):
        return 0
    open_hour, close_hour = panel_window(config)
    if open_hour == close_hour:
        return 0
    current = now or datetime.now(_PANEL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_PANEL_TZ)
    current = current.astimezone(_PANEL_TZ)
    if current.hour == open_hour:
        return await notify_panel_window("open", config)
    if current.hour == close_hour:
        return await notify_panel_window("close", config)
    return 0
