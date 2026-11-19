"""命令层公共辅助：图片发送、UID/游戏判定、异常包装"""
from __future__ import annotations

import asyncio
import functools
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment  # noqa: F401
from nonebot.exception import MatcherException
from nonebot.matcher import Matcher

from ..core import store
from ..core.panel_quota import (
    has_panel_draws,
    panel_closed_message,
    panel_enabled,
    panel_time_allowed,
)
from ..datasource.errors import ProfileError
from ..datasource.gacha_log import GachaLogError

# 消息文本中的 UID：9-10 位数字，18 开头允许 11 位（与 store._UID_RE 同规则，但用于 search）
_UID_RE = re.compile(r"(18|[1-9])\d{8,9}")

# 业务异常：直接回中文提示
_BIZ_ERRORS = (GachaLogError, ProfileError, ValueError)


async def _delete_tmp_after(path: str, delay: int = 30) -> None:
    """延迟删除临时文件，给 OneBot 实现留出读取本地文件的时间"""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError as e:
        logger.warning(f"[miao] 删除临时文件失败: {path} - {e}")


async def ensure_panel_enabled(matcher: type[Matcher]) -> None:
    """面板总开关关闭时直接结束当前指令。"""
    if panel_enabled():
        return
    await matcher.finish("面板功能已关闭，SUPERUSER 发送 /面板开关 开 可恢复")


async def ensure_panel_available(
    matcher: type[Matcher],
    event: Any | None = None,
    *,
    feature: str | None = None,
) -> None:
    """总开关 + 开放时段检查；不在开放时段时直接结束当前指令。

    传入 ``feature``（角色面板 / 群面板排名）时，若调用者还有转盘抽到的对应
    次数，则不受开放时段限制，交给 ``authorize_panel_call`` 优先消耗。
    """
    await ensure_panel_enabled(matcher)
    if event is None or panel_time_allowed():
        return
    if feature is not None and await has_panel_draws(event, feature):
        return
    await matcher.finish(panel_closed_message())


async def send_image(matcher: type[Matcher], png_bytes: bytes) -> None:
    """直接以 base64 发送 PNG

    macOS 上 QQ/NapCat 进程带 App Sandbox，无权读取本进程在 /tmp 下创建的
    临时文件（内核 deny file-read-data，OneBot 侧报 EPERM），因此不走
    file:// 临时文件方案；bytes 会由 onebot 适配器编码为 base64:// 发送。
    """
    await matcher.send(MessageSegment.image(png_bytes))


def resolve_uid(event: MessageEvent, args_text: str, game: str) -> str | None:
    """确定要操作的 UID：消息文本中的 UID 优先，否则用绑定的 UID，都没有返回 None"""
    for text in (args_text or "", event.get_plaintext()):
        m = _UID_RE.search(text)
        if m:
            return m.group(0)
    return store.get_uid(event.get_user_id(), game)


def is_sr(text: str) -> bool:
    """文本含“星铁”则视为星穹铁道指令"""
    return "星铁" in (text or "")


def game_of(text: str) -> str:
    """按消息文本判定游戏："gs" 原神 / "sr" 星铁"""
    return "sr" if is_sr(text) else "gs"


def guard(matcher: type[Matcher]) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """处理器异常包装装饰器：业务异常回中文提示，未知异常记日志后回通用文案

    matcher.finish 抛出的 MatcherException 必须原样上抛，否则响应流程会被打断。
    functools.wraps 保留原签名，不影响 nonebot 的参数注入。
    """

    def deco(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except MatcherException:
                raise
            except _BIZ_ERRORS as e:
                await matcher.finish(str(e))
            except Exception:
                logger.exception(f"[miao] 指令处理出错: {func.__name__}")
                await matcher.finish("出错了，请稍后再试")

        return wrapper

    return deco
