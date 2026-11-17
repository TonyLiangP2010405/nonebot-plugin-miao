"""角色面板查看配额：与 nonebot-plugin-mantou-affection 联动。

规则（群聊）：
- 每人每小时前 ``MIAO_PANEL_FREE_PER_HOUR`` 次（默认 2）免费；
- 每人每小时最多 ``MIAO_PANEL_USER_HOURLY_LIMIT`` 次（默认 4）；
- 馒头命运齿轮转盘抽到的面板次数优先消耗，且不占用免费额度；
- 免费次数用完后，第 3、4 次每次消耗 ``MIAO_PANEL_AFFECTION_COST`` 点好感度
  （默认 10），并且需要调用者回复「确认」才会扣费出图；
- 私聊免费；SUPERUSER 免费、不限次数；
- 未安装或未加载馒头好感度插件时不做限制，保证本插件可以独立使用。
"""
from __future__ import annotations

import importlib
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
    needs_confirm: bool = False
    used_draw: bool = False
    paid: bool = False


@dataclass
class PendingPanel:
    """等待调用者回复「确认」的付费面板请求。"""

    group_id: str
    user_id: str
    avatar: dict[str, Any]
    game: str
    uid: str
    show_dmg: bool
    dmg_idx: int | None
    created_at: float = field(default_factory=time.time)


_API: MantouAPI | None | bool = None
_API_LOGGED = False
_PENDING: dict[tuple[str, str], PendingPanel] = {}


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


def panel_enabled(config: Any | None = None) -> bool:
    """面板功能当前是否开启：运行时覆盖优先，否则读取配置默认值。"""
    override = store.get_panel_enabled_override()
    if override is not None:
        return override
    config = config or _plugin_config()
    return bool(getattr(config, "miao_panel_enabled", True))


def panel_enabled_source() -> str:
    """当前开关来源（运行时覆盖 / 配置文件）。"""
    return "运行时覆盖" if store.get_panel_enabled_override() is not None else "配置文件"


def clear_all_pending() -> int:
    """清空所有待确认请求，返回被取消的数量。"""
    count = len(_PENDING)
    _PENDING.clear()
    return count


def set_panel_enabled(value: bool | None) -> int:
    """设置/清除面板总开关；关闭时顺带取消所有待确认请求，返回取消数量。"""
    store.set_panel_enabled_override(value)
    return clear_all_pending() if value is False else 0


def confirm_timeout(config: Any | None = None) -> int:
    """付费面板确认的有效秒数。"""
    config = config or _plugin_config()
    return max(5, _int_attr(config, "miao_panel_confirm_timeout", 30))


# ---------------------------------------------------------------------------
# 待确认请求
# ---------------------------------------------------------------------------


def register_pending(
    event: Any,
    *,
    avatar: dict[str, Any],
    game: str,
    uid: str,
    show_dmg: bool,
    dmg_idx: int | None,
) -> PendingPanel:
    """登记一次等待「确认」的付费面板请求（同一个人只保留最新一条）。"""
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.get_user_id())
    pending = PendingPanel(
        group_id=group_id,
        user_id=user_id,
        avatar=avatar,
        game=game,
        uid=uid,
        show_dmg=show_dmg,
        dmg_idx=dmg_idx,
    )
    _PENDING[(group_id, user_id)] = pending
    return pending


def has_pending(group_id: str | int, user_id: str | int, timeout: int) -> bool:
    key = (str(group_id), str(user_id))
    pending = _PENDING.get(key)
    if pending is None:
        return False
    if time.time() - pending.created_at > timeout:
        _PENDING.pop(key, None)
        return False
    return True


def clear_pending(group_id: str | int, user_id: str | int) -> PendingPanel | None:
    """丢弃某人的待确认请求（新的面板请求/免费查看会覆盖旧请求）。"""
    return _PENDING.pop((str(group_id), str(user_id)), None)


def pop_pending(group_id: str | int, user_id: str | int, timeout: int) -> PendingPanel | None:
    key = (str(group_id), str(user_id))
    pending = _PENDING.pop(key, None)
    if pending is None:
        return None
    if time.time() - pending.created_at > timeout:
        return None
    return pending


# ---------------------------------------------------------------------------
# 授权
# ---------------------------------------------------------------------------


async def authorize_panel_call(event: Any, config: Any | None = None, *, confirmed: bool = False) -> PanelCheck:
    """群聊面板查看授权：本小时上限 → 转盘次数 → 免费次数 → 付费确认。"""
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
    free = _int_attr(config, "miao_panel_free_per_hour", 2)
    limit = _int_attr(config, "miao_panel_user_hourly_limit", 4)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)
    timeout = confirm_timeout(config)

    calls = store.panel_user_calls(group_id, user_id)
    if limit > 0 and calls >= limit:
        wait = store.panel_user_wait(group_id, user_id, limit)
        minutes = max(1, math.ceil(wait / 60))
        return PanelCheck(
            False,
            f"你本小时已查看 {calls} 次角色面板，请约 {minutes} 分钟后再试",
        )

    try:
        draws = int(await api.get_panel_draws(group_id, user_id) or 0)
        if draws > 0:
            remaining = await api.consume_panel_draw(group_id, user_id)
            if remaining is not None:
                store.record_panel_user_call(group_id, user_id, "draw")
                return PanelCheck(True, used_draw=True)

        free_used = store.panel_user_free_used(group_id, user_id)
        if free_used < free:
            store.record_panel_user_call(group_id, user_id, "free")
            return PanelCheck(True)

        affection = int(await api.get_affection(group_id, user_id) or 0)
        if cost <= 0:
            return PanelCheck(False, "本小时免费次数已用完")
        if affection < cost:
            return PanelCheck(
                False,
                f"本小时免费次数已用完，且好感度不足 {cost} 点（当前 {affection}）。"
                "参与馒头命运齿轮转盘有 5% 概率抽到面板次数。",
            )
        if not confirmed:
            return PanelCheck(
                False,
                needs_confirm=True,
                message=(
                    f"本小时免费次数已用完。本次查看将消耗 {cost} 点好感度（当前 {affection}），"
                    f"回复「确认」继续，回复「取消」放弃（{timeout} 秒内有效）。"
                ),
            )
        ok, current = await api.spend_affection(
            group_id,
            user_id,
            cost,
            nickname=_nickname(event),
            source="nonebot_plugin_miao:panel",
        )
        if ok:
            store.record_panel_user_call(group_id, user_id, "paid")
            return PanelCheck(True, paid=True)
        return PanelCheck(
            False,
            f"好感度不足 {cost} 点（当前 {current}），本次面板查看已取消。",
        )
    except Exception:
        logger.warning("[miao] 面板配额检查失败，本次放行", exc_info=True)
        return PanelCheck(True)


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
    free = _int_attr(config, "miao_panel_free_per_hour", 2)
    limit = _int_attr(config, "miao_panel_user_hourly_limit", 4)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)
    try:
        draws = int(await api.get_panel_draws(group_id, user_id) or 0)
        affection = int(await api.get_affection(group_id, user_id) or 0)
    except Exception:
        logger.warning("[miao] 读面板次数失败", exc_info=True)
        return "面板次数暂时读取失败，请稍后再试。"

    calls = store.panel_user_calls(group_id, user_id)
    free_left = max(0, free - store.panel_user_free_used(group_id, user_id))
    wait = store.panel_user_wait(group_id, user_id, limit)
    quota_line = f"本小时已查看：{calls}/{limit} 次" if limit > 0 else f"本小时已查看：{calls} 次"
    if wait > 0:
        quota_line += f"（约 {max(1, math.ceil(wait / 60))} 分钟后恢复）"
    return "\n".join(
        [
            f"转盘面板次数：{draws} 次（优先消耗）",
            f"本小时免费次数：{free_left}/{free} 次",
            quota_line,
            f"当前好感度：{affection} 点"
            + (f"（免费次数用完后每次消耗 {cost} 点，需回复「确认」）" if cost > 0 else ""),
            "面板次数获取：参与馒头命运齿轮转盘，有 5% 概率抽到 ×1。",
        ]
    )
