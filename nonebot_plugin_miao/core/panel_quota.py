"""角色面板 / 群面板排名配额：与 nonebot-plugin-mantou-affection 联动。

规则（群聊）：
- 面板功能每天 ``MIAO_PANEL_OPEN_HOUR`` 点到 ``MIAO_PANEL_CLOSE_HOUR`` 点开放
  （默认 08:00–17:00，Asia/Shanghai）；
- 角色面板、群面板排名**各自独立**：每人每小时免费 1 次、最多 2 次；
- 免费次数用完后，第 2 次消耗 ``MIAO_PANEL_AFFECTION_COST`` 点好感度
  （默认 10），并且需要调用者回复「确认」才会扣费出图；
- 馒头命运齿轮转盘抽到的面板 / 群排名次数优先消耗，**不占用免费额度，
  也不计入每小时 2 次上限**，且**不受开放时段限制**；
- 非开放时段仍可查看：有转盘次数优先用，否则每次消耗
  ``MIAO_PANEL_OFFHOUR_AFFECTION_COST`` 点好感度（默认 50），同样回复
  「确认」后扣费，不占用免费额度与每小时上限；
- 私聊免费；SUPERUSER 免费、不限次数；
- 未安装或未加载馒头好感度插件时不做限制，保证本插件可以独立使用。
"""
from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from nonebot import get_loaded_plugins, logger

from . import store

try:  # Windows 未安装 tzdata 时退回固定 UTC+8
    from zoneinfo import ZoneInfo

    _PANEL_TZ: Any = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - 取决于运行环境
    _PANEL_TZ = timezone(timedelta(hours=8))

_MANTOU_PLUGIN = "nonebot_plugin_mantou_affection"

FEATURE_DETAIL = "detail"
FEATURE_RANK = "rank"
_FEATURE_NAME = {FEATURE_DETAIL: "角色面板", FEATURE_RANK: "群面板排名"}
_PAID_SOURCE = {
    FEATURE_DETAIL: "nonebot_plugin_miao:panel",
    FEATURE_RANK: "nonebot_plugin_miao:panel_rank",
}
_OFFHOUR_SOURCE = {
    FEATURE_DETAIL: "nonebot_plugin_miao:panel_offhour",
    FEATURE_RANK: "nonebot_plugin_miao:panel_rank_offhour",
}


@dataclass(frozen=True)
class MantouAPI:
    """用到的馒头好感度插件 API 子集。"""

    get_panel_draws: Callable[..., Awaitable[int]]
    consume_panel_draw: Callable[..., Awaitable[int | None]]
    get_affection: Callable[..., Awaitable[int]]
    spend_affection: Callable[..., Awaitable[tuple[bool, int]]]
    # 0.44.0 起提供；旧版本没有群排名账本时按 0 次处理
    get_panel_rank_draws: Callable[..., Awaitable[int]] | None = None
    consume_panel_rank_draw: Callable[..., Awaitable[int | None]] | None = None
    add_panel_draws: Callable[..., Awaitable[int]] | None = None
    add_panel_rank_draws: Callable[..., Awaitable[int]] | None = None


@dataclass(frozen=True)
class PanelCheck:
    """一次面板查看的授权结果。"""

    allowed: bool
    message: str = ""
    needs_confirm: bool = False
    used_draw: bool = False
    paid: bool = False
    feature: str = FEATURE_DETAIL
    # 需要「确认」时本次将消耗的好感度，用于锁定报价
    cost: int = 0


@dataclass
class PendingPanel:
    """等待调用者回复「确认」的付费请求。"""

    group_id: str
    user_id: str
    feature: str
    payload: dict[str, Any]
    cost: int = 0
    created_at: float = field(default_factory=time.time)


_API: MantouAPI | None = None
_API_LOGGED = False
_PENDING: dict[tuple[str, str], PendingPanel] = {}


def mantou_api() -> MantouAPI | None:
    """软接入馒头好感度插件；不可用时返回 None 并只记一次日志。"""
    global _API, _API_LOGGED
    if _API is not None:
        return _API
    # 从已加载插件取模块，兼容通过本地 plugins 目录加载的馒头插件。
    module = next(
        (plugin.module for plugin in get_loaded_plugins() if plugin.name == _MANTOU_PLUGIN),
        None,
    )
    try:
        _API = MantouAPI(
            get_panel_draws=module.get_panel_draws,
            consume_panel_draw=module.consume_panel_draw,
            get_affection=module.get_affection,
            spend_affection=module.spend_affection,
            get_panel_rank_draws=getattr(module, "get_panel_rank_draws", None),
            consume_panel_rank_draw=getattr(module, "consume_panel_rank_draw", None),
            add_panel_draws=getattr(module, "add_panel_draws", None),
            add_panel_rank_draws=getattr(module, "add_panel_rank_draws", None),
        )
    except AttributeError:
        if not _API_LOGGED:
            logger.info("[miao] 未检测到可用的馒头好感度联动接口，面板次数限制不启用")
            _API_LOGGED = True
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


# ---------------------------------------------------------------------------
# 开放时段
# ---------------------------------------------------------------------------


def panel_window(config: Any | None = None) -> tuple[int, int]:
    """面板开放时段 (开启小时, 关闭小时)，默认 08:00–17:00。"""
    config = config or _plugin_config()
    open_hour = min(23, max(0, _int_attr(config, "miao_panel_open_hour", 8)))
    close_hour = min(23, max(0, _int_attr(config, "miao_panel_close_hour", 17)))
    return open_hour, close_hour


def _local_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(_PANEL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_PANEL_TZ)
    return current.astimezone(_PANEL_TZ)


def panel_time_allowed(config: Any | None = None, now: datetime | None = None) -> bool:
    """当前是否处于面板开放时段；起止相同表示全天开放。"""
    open_hour, close_hour = panel_window(config)
    if open_hour == close_hour:
        return True
    hour = _local_now(now).hour
    if open_hour < close_hour:
        return open_hour <= hour < close_hour
    # 跨天，例如 22:00–06:00
    return hour >= open_hour or hour < close_hour


def panel_window_text(config: Any | None = None) -> str:
    open_hour, close_hour = panel_window(config)
    if open_hour == close_hour:
        return "全天开放"
    return f"{open_hour:02d}:00–{close_hour:02d}:00"


def panel_closed_message(config: Any | None = None, now: datetime | None = None) -> str:
    """不在开放时段时的提示，包含下一次开启时间。"""
    config = config or _plugin_config()
    open_hour, _ = panel_window(config)
    current = _local_now(now)
    next_open = current.replace(hour=open_hour, minute=0, second=0, microsecond=0)
    if next_open <= current:
        next_open += timedelta(days=1)
    day = "今天" if next_open.date() == current.date() else "明天"
    offhour = _int_attr(config, "miao_panel_offhour_affection_cost", 50)
    offhour_hint = (
        f"没有转盘次数时，群聊可消耗 {offhour} 点好感度查看（回复「确认」后扣费）。\n"
        if offhour > 0
        else ""
    )
    return (
        f"面板功能每天 {panel_window_text(config)} 开放，当前已关闭，"
        f"{day} {open_hour:02d}:00 后再试。\n"
        f"{offhour_hint}"
        "提示：转盘抽到的面板 / 群排名次数不受时段限制，可直接使用。"
    )


async def has_panel_draws(event: Any, feature: str = FEATURE_DETAIL) -> bool:
    """是否还有转盘抽到的对应面板次数（有则不受开放时段限制）。"""
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return False
    user_id = str(event.get_user_id())
    if is_superuser(user_id):
        return False
    api = mantou_api()
    if api is None:
        return False
    get_draws, consume_draw = _draw_calls(api, feature)
    if get_draws is None or consume_draw is None:
        return False
    try:
        return int(await get_draws(group_id, user_id) or 0) > 0
    except Exception:
        logger.warning("[miao] 读取转盘面板次数失败", exc_info=True)
        return False


def panel_offhour_access(event: Any) -> bool:
    """非开放时段是否还能尝试查看：可扣好感度付费，或 SUPERUSER 免费。"""
    if getattr(event, "group_id", None) is None:
        return False
    if is_superuser(str(event.get_user_id())):
        return True
    config = _plugin_config()
    if _int_attr(config, "miao_panel_offhour_affection_cost", 50) <= 0:
        return False
    return mantou_api() is not None


# ---------------------------------------------------------------------------
# 总开关
# ---------------------------------------------------------------------------


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


def update_interval(config: Any | None = None) -> int:
    """更新面板命令冷却秒数（默认 3600；0 表示不限制）。"""
    config = config or _plugin_config()
    return max(0, _int_attr(config, "miao_panel_update_interval", 60)) * 60


def update_wait(user_id: str | int, game: str, config: Any | None = None) -> int:
    """距离下次可用 /更新面板 还有多少秒。"""
    return store.panel_update_wait(user_id, game, update_interval(config))


def mark_update(user_id: str | int, game: str, config: Any | None = None) -> None:
    """记录一次成功的更新面板。"""
    store.mark_panel_update(user_id, game, update_interval(config))


def update_limit_message(wait: int, config: Any | None = None) -> str:
    interval = update_interval(config)
    if interval == 3600:
        rule = "每小时"
    elif interval > 0 and interval % 60 == 0:
        rule = f"每 {interval // 60} 分钟"
    else:
        rule = f"每 {max(1, interval)} 秒"
    minutes = max(1, math.ceil(wait / 60))
    return f"更新面板{rule}只能使用 1 次，请 {minutes} 分钟后再试"


# ---------------------------------------------------------------------------
# 待确认请求
# ---------------------------------------------------------------------------


def register_pending(
    event: Any,
    *,
    feature: str = FEATURE_DETAIL,
    payload: dict[str, Any] | None = None,
    cost: int = 0,
) -> PendingPanel:
    """登记一次等待「确认」的付费请求（同一个人只保留最新一条）。"""
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.get_user_id())
    pending = PendingPanel(
        group_id=group_id,
        user_id=user_id,
        feature=feature,
        payload=dict(payload or {}),
        cost=max(0, int(cost or 0)),
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


def _draw_calls(api: MantouAPI, feature: str) -> tuple[Any, Any]:
    if feature == FEATURE_RANK:
        return api.get_panel_rank_draws, api.consume_panel_rank_draw
    return api.get_panel_draws, api.consume_panel_draw


async def adjust_panel_draws(group_id: int, user_id: str, feature: str, delta: int) -> str:
    """通过馒头 API 增减同一群内的转盘次数，余额不足时整笔拒绝。"""
    label = _FEATURE_NAME[feature]
    api = mantou_api()
    if api is None:
        raise ValueError("请先加载兼容版本的馒头好感度插件。")
    get_draws, consume_draw = _draw_calls(api, feature)
    add_draws = api.add_panel_rank_draws if feature == FEATURE_RANK else api.add_panel_draws
    change_draws = add_draws if delta > 0 else consume_draw
    if get_draws is None or change_draws is None:
        raise ValueError(f"当前馒头好感度插件不支持调整{label}次数，请升级后再试。")
    remaining = await change_draws(group_id, user_id, abs(delta))
    if remaining is None:
        current = await get_draws(group_id, user_id)
        return f"{label}次数不足，扣除失败，当前剩余 {current} 次。"
    return f"{label}次数已调整 {delta:+d}，当前剩余 {remaining} 次。"


async def authorize_panel_call(
    event: Any,
    config: Any | None = None,
    *,
    confirmed: bool = False,
    feature: str = FEATURE_DETAIL,
    quoted_cost: int | None = None,
) -> PanelCheck:
    """群聊面板授权：转盘次数 → 非开放时段付费 / 小时上限 → 免费 → 付费确认。

    ``quoted_cost`` 是「确认」提示里报给用户的价格；确认时若当前价格更高，
    按较低者扣费，避免跨开放时段边界时多扣好感度。
    """
    if feature not in (FEATURE_DETAIL, FEATURE_RANK):
        feature = FEATURE_DETAIL
    label = _FEATURE_NAME[feature]
    group_id = getattr(event, "group_id", None)
    user_id = str(event.get_user_id())
    config = config or _plugin_config()
    closed = not panel_time_allowed(config)
    if group_id is None:
        # 私聊免费；非开放时段私聊没有好感度账本，仍然关闭
        if closed:
            return PanelCheck(False, panel_closed_message(config), feature=feature)
        return PanelCheck(True, feature=feature)
    if is_superuser(user_id):
        # SUPERUSER 免费不限，且不占本群额度
        return PanelCheck(True, feature=feature)
    api = mantou_api()
    if api is None:
        if closed:
            return PanelCheck(False, panel_closed_message(config), feature=feature)
        return PanelCheck(True, feature=feature)

    free = _int_attr(config, "miao_panel_free_per_hour", 1)
    limit = _int_attr(config, "miao_panel_user_hourly_limit", 2)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)
    offhour_cost = _int_attr(config, "miao_panel_offhour_affection_cost", 50)
    timeout = confirm_timeout(config)

    try:
        # 转盘次数优先，且不受开放时段与每小时上限限制
        _, consume_draw = _draw_calls(api, feature)
        if consume_draw is not None:
            remaining = await consume_draw(group_id, user_id)
            if remaining is not None:
                return PanelCheck(True, used_draw=True, feature=feature)

        # 非开放时段：每次消耗固定好感度，不占免费额度与每小时上限
        if closed:
            charge = offhour_cost
            if confirmed and quoted_cost:
                charge = min(charge, quoted_cost)
            if charge <= 0:
                return PanelCheck(False, panel_closed_message(config), feature=feature)
            affection = int(await api.get_affection(group_id, user_id) or 0)
            if affection < charge:
                chance = "5%" if feature == FEATURE_DETAIL else "3%"
                command = "面板次数" if feature == FEATURE_DETAIL else "群面板排名次数"
                return PanelCheck(
                    False,
                    f"当前非面板开放时段（{panel_window_text(config)} 外），"
                    f"查看{label}需消耗 {charge} 点好感度"
                    f"（当前 {affection}）。参与馒头命运齿轮转盘有 {chance} 概率"
                    f"抽到{command}，或等 {panel_window_text(config)} 开放后再试。",
                    feature=feature,
                )
            if not confirmed:
                return PanelCheck(
                    False,
                    needs_confirm=True,
                    cost=charge,
                    message=(
                        f"当前非面板开放时段（{panel_window_text(config)} 外），"
                        f"本次查看{label}将消耗 {charge} 点好感度"
                        f"（当前 {affection}），回复「确认」或发送 /面板确认 继续，回复「取消」放弃"
                        f"（{timeout} 秒内有效）。"
                    ),
                    feature=feature,
                )
            ok, current = await api.spend_affection(
                group_id,
                user_id,
                charge,
                nickname=_nickname(event),
                source=_OFFHOUR_SOURCE[feature],
            )
            if ok:
                return PanelCheck(True, paid=True, feature=feature)
            return PanelCheck(
                False,
                f"好感度不足 {charge} 点（当前 {current}），本次{label}查看已取消。",
                feature=feature,
            )

        calls = store.panel_user_calls(group_id, user_id, feature=feature)
        if limit > 0 and calls >= limit:
            wait = store.panel_user_wait(group_id, user_id, limit, feature=feature)
            minutes = max(1, math.ceil(wait / 60))
            return PanelCheck(
                False,
                f"你本小时已查看 {calls} 次{label}，请约 {minutes} 分钟后再试",
                feature=feature,
            )

        free_used = store.panel_user_free_used(group_id, user_id, feature=feature)
        if free_used < free:
            store.record_panel_user_call(group_id, user_id, "free", feature=feature)
            return PanelCheck(True, feature=feature)

        affection = int(await api.get_affection(group_id, user_id) or 0)
        if cost <= 0:
            return PanelCheck(False, "本小时免费次数已用完", feature=feature)
        if confirmed and quoted_cost:
            cost = min(cost, quoted_cost)
        if affection < cost:
            chance = "5%" if feature == FEATURE_DETAIL else "3%"
            command = "面板次数" if feature == FEATURE_DETAIL else "群面板排名次数"
            return PanelCheck(
                False,
                f"本小时免费次数已用完，且好感度不足 {cost} 点（当前 {affection}）。"
                f"参与馒头命运齿轮转盘有 {chance} 概率抽到{command}。",
                feature=feature,
            )
        if not confirmed:
            return PanelCheck(
                False,
                needs_confirm=True,
                cost=cost,
                message=(
                    f"本小时免费次数已用完。本次查看{label}将消耗 {cost} 点好感度"
                    f"（当前 {affection}），回复「确认」或发送 /面板确认 继续，回复「取消」放弃"
                    f"（{timeout} 秒内有效）。"
                ),
                feature=feature,
            )
        ok, current = await api.spend_affection(
            group_id,
            user_id,
            cost,
            nickname=_nickname(event),
            source=_PAID_SOURCE[feature],
        )
        if ok:
            store.record_panel_user_call(group_id, user_id, "paid", feature=feature)
            return PanelCheck(True, paid=True, feature=feature)
        return PanelCheck(
            False,
            f"好感度不足 {cost} 点（当前 {current}），本次{label}查看已取消。",
            feature=feature,
        )
    except Exception:
        logger.warning("[miao] 面板配额检查失败", exc_info=True)
        return PanelCheck(False, "暂时无法核对面板次数或好感度，请稍后重试。", feature=feature)


async def panel_status(event: Any, config: Any | None = None) -> str:
    """`/面板次数` 的状态文案。"""
    config = config or _plugin_config()
    window = panel_window_text(config)
    opened = panel_time_allowed(config)
    time_line = f"面板开放时间：{window}（当前{'开放' if opened else '已关闭'}）"
    offhour = _int_attr(config, "miao_panel_offhour_affection_cost", 50)
    group_id = getattr(event, "group_id", None)
    user_id = str(event.get_user_id())
    if group_id is None:
        hint = f"\n群聊非开放时段每次消耗 {offhour} 点好感度。" if offhour > 0 else ""
        return (
            f"{time_line}\n私聊查看角色面板与群排名免费（仅开放时段），"
            f"不消耗面板次数。{hint}"
        )
    if is_superuser(user_id):
        return f"{time_line}\nSUPERUSER 不限次数，免费查看角色面板与群面板排名。"
    api = mantou_api()
    if api is None:
        return f"{time_line}\n未加载馒头好感度插件，面板次数与好感度限制暂未启用。"

    free = _int_attr(config, "miao_panel_free_per_hour", 1)
    limit = _int_attr(config, "miao_panel_user_hourly_limit", 2)
    cost = _int_attr(config, "miao_panel_affection_cost", 10)
    try:
        draws = int(await api.get_panel_draws(group_id, user_id) or 0)
        rank_draws = 0
        if api.get_panel_rank_draws is not None:
            rank_draws = int(await api.get_panel_rank_draws(group_id, user_id) or 0)
        affection = int(await api.get_affection(group_id, user_id) or 0)
    except Exception:
        logger.warning("[miao] 读面板次数失败", exc_info=True)
        return "面板次数暂时读取失败，请稍后再试。"

    def _feature_line(name: str, feature: str, draw_count: int) -> list[str]:
        calls = store.panel_user_calls(group_id, user_id, feature=feature)
        free_left = max(0, free - store.panel_user_free_used(group_id, user_id, feature=feature))
        wait = store.panel_user_wait(group_id, user_id, limit, feature=feature)
        quota = f"已查看 {calls}/{limit} 次" if limit > 0 else f"已查看 {calls} 次"
        if wait > 0:
            quota += f"（约 {max(1, math.ceil(wait / 60))} 分钟后恢复）"
        return [
            f"{name}：转盘次数 {draw_count} 次（优先消耗，不占额度）",
            f"{name}：免费剩余 {free_left}/{free} 次，{quota}",
        ]

    lines = [time_line]
    lines.extend(_feature_line("角色面板", FEATURE_DETAIL, draws))
    lines.extend(_feature_line("群面板排名", FEATURE_RANK, rank_draws))
    lines.append(f"当前好感度：{affection} 点")
    if cost > 0:
        lines.append(f"免费次数用完后每次消耗 {cost} 点好感度，需回复「确认」")
    if offhour > 0:
        lines.append(f"非开放时段每次消耗 {offhour} 点好感度，需回复「确认」")
    if cost > 0 or offhour > 0:
        lines.append("也可以发送 /面板确认 或 /面板取消。")
    lines.append("转盘抽到的面板 / 群排名次数不受开放时段限制，任何时间都可以使用。")
    lines.append("面板次数获取：参与馒头命运齿轮转盘，角色面板 5%、群面板排名 3%。")
    return "\n".join(lines)
