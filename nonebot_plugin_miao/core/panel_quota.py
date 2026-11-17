"""角色面板查看配额：与 nonebot-plugin-mantou-affection 联动。

规则：
- 私聊免费，不限次数；
- SUPERUSER 免费，不限次数，也不占本群额度；
- 群聊每小时最多 ``MIAO_PANEL_GROUP_HOURLY_LIMIT`` 次（默认 5）；
- 优先消耗馒头命运齿轮转盘抽到的「面板次数」，没有次数时消耗
  ``MIAO_PANEL_AFFECTION_COST`` 点好感度（默认 10）；
- 未安装或未加载馒头好感度插件时不做限制，保证本插件可以独立使用。
"""
from __future__ import annotations

import importlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nonebot import logger

from . import store

_MANTOU_PLUGIN = "nonebot_plugin_mantou_affection"


@dataclass(frozen=True)
class MantouAPI:
    """用到的馒头好感度插件 API 子集。"""

    get_panel_draws: Callable[..., Awaitable[int]]
    consume_panel_draw: Callable[..., Awaitable[int | None]]
    get_affection: Callable[..., Awaitable[int]]
    spend_affection: Callable[..., Awaitable[tuple[bool, int]]]


@dataclass(frozen=True)
class PanelCheck:
    """一次面板查看的授权结果。"""

    allowed: bool
    message: str = ""
    used_draw: bool = False
    paid: bool = False


_API: MantouAPI | None | bool = None
_API_LOGGED = False


def mantou_api() -> MantouAPI | None:
    """软接入馒头好感度插件；不可用时返回 None 并只记一次日志。"""
    global _API, _API_LOGGED
    if _API is False:
        return None
    if _API is not None:
        return _API
    try:
        from nonebot import get_plugin

        if get_plugin(_MANTOU_PLUGIN) is None:
            _API = False
            if not _API_LOGGED:
                logger.info("[miao] 未加载 nonebot-plugin-mantou-affection，面板次数限制不启用")
                _API_LOGGED = True
            return None
    except Exception:
        # nonebot 未初始化（脚本/测试）时继续尝试导入
        pass
    try:
        module = importlib.import_module(_MANTOU_PLUGIN)
        _API = MantouAPI(
            get_panel_draws=module.get_panel_draws,
            consume_panel_draw=module.consume_panel_draw,
            get_affection=module.get_affection,
            spend_affection=module.spend_affection,
        )
    except Exception:
        _API = False
        if not _API_LOGGED:
            logger.info("[miao] 未检测到可用的馒头好感度联动接口，面板次数限制不启用")
            _API_LOGGED = True
        return None
    return _API


def is_superuser(user_id: str | int) -> bool:
    """是否 NoneBot SUPERUSER（读取失败时按普通用户处理）。"""
    try:
        from nonebot import get_driver

        return str(user_id) in {str(uid) for uid in get_driver().config.superusers}
    except Exception:
        return False


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


def _nickname(event: Any) -> str:
    sender = getattr(event, "sender", None)
    card = getattr(sender, "card", "") if sender is not None else ""
    nick = getattr(sender, "nickname", "") if sender is not None else ""
    return str(card or nick or "")


async def authorize_panel_call(event: Any, config: Any | None = None) -> PanelCheck:
    """群聊面板查看授权：本群限流 → 面板次数 → 好感度。"""
    group_id = getattr(event, "group_id", None)
    user_id = str(event.get_user_id())
    if group_id is None:
        # 私聊免费
        return PanelCheck(True)
    if is_superuser(user_id):
        # SUPERUSER 免费不限，且不占本群额度
        return PanelCheck(True)
    api = mantou_api()
    if api is None:
        return PanelCheck(True)

    config = config or _plugin_config()
    limit = _int_attr(config, "miao_panel_group_hourly_limit", 5)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)

    wait = store.panel_group_wait(group_id, limit)
    if wait > 0:
        minutes = max(1, math.ceil(wait / 60))
        return PanelCheck(
            False,
            f"本群每小时最多查看 {limit} 次角色面板，请约 {minutes} 分钟后再试",
        )

    try:
        draws = int(await api.get_panel_draws(group_id, user_id) or 0)
        if draws > 0:
            remaining = await api.consume_panel_draw(group_id, user_id)
            if remaining is not None:
                store.record_panel_group_call(group_id)
                return PanelCheck(True, used_draw=True)

        affection = int(await api.get_affection(group_id, user_id) or 0)
        if cost > 0 and affection >= cost:
            ok, current = await api.spend_affection(
                group_id,
                user_id,
                cost,
                nickname=_nickname(event),
                source="nonebot_plugin_miao:panel",
            )
            if ok:
                store.record_panel_group_call(group_id)
                return PanelCheck(True, paid=True)
            affection = current
    except Exception:
        logger.warning("[miao] 面板配额检查失败，本次放行", exc_info=True)
        return PanelCheck(True)

    if cost > 0:
        message = (
            f"面板次数不足，且好感度不足 {cost} 点（当前 {affection}）。"
            "参与馒头命运齿轮转盘有 5% 概率抽到面板次数。"
        )
    else:
        message = "面板次数不足，参与馒头命运齿轮转盘有 5% 概率抽到面板次数。"
    return PanelCheck(False, message)


async def panel_status(event: Any, config: Any | None = None) -> str:
    """`/面板次数` 的状态文案。"""
    group_id = getattr(event, "group_id", None)
    user_id = str(event.get_user_id())
    if group_id is None:
        return "私聊查看角色面板免费，不消耗面板次数。"
    if is_superuser(user_id):
        return "SUPERUSER 不限次数，免费查看角色面板。"
    api = mantou_api()
    if api is None:
        return "未加载馒头好感度插件，角色面板次数与好感度限制暂未启用。"

    config = config or _plugin_config()
    limit = _int_attr(config, "miao_panel_group_hourly_limit", 5)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)
    try:
        draws = int(await api.get_panel_draws(group_id, user_id) or 0)
        affection = int(await api.get_affection(group_id, user_id) or 0)
    except Exception:
        logger.warning("[miao] 读取面板次数失败", exc_info=True)
        return "面板次数暂时读取失败，请稍后再试。"

    used = store.panel_group_used(group_id)
    wait = store.panel_group_wait(group_id, limit)
    quota_line = f"本群本小时已查看：{used}/{limit} 次"
    if wait > 0:
        quota_line += f"（约 {max(1, math.ceil(wait / 60))} 分钟后恢复）"
    return "\n".join(
        [
            f"你的角色面板次数：{draws} 次",
            f"当前好感度：{affection} 点"
            + (f"（次数用完时每次消耗 {cost} 点）" if cost > 0 else ""),
            quota_line,
            "面板次数获取：参与馒头命运齿轮转盘，有 5% 概率抽到 ×1。",
        ]
    )
