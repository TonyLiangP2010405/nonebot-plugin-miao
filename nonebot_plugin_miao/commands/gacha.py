"""抽卡相关指令：authkey 嗅探 / 更新记录 / 记录分析 / 统计 / 模拟抽卡 / 定轨 / UIGF 导入导出

正则设计要点（交叉命中都有测试覆盖）：
- 记录分析 / 统计要求以 `/` 开头，并以 记录|祈愿|分析 / 统计结尾
- 模拟抽卡是唯一无需 `/` 的功能，同时兼容旧的 `#` 前缀
- authkey 嗅探优先级最低（15），避免与正常指令抢消息
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile

import httpx
from nonebot import get_plugin_config, logger, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.permission import SUPERUSER

from ..config import Config
from ..core import store
from ..datasource.gacha_log import (
    DEFAULT_POOLS,
    GACHA_TYPES,
    fetch_gacha_log,
    load_authkey,
    parse_authkey_url,
    save_authkey,
    update_gacha_log,
)
from ..datasource.uigf import export_uigf, import_uigf
from ..gacha.analyse import analyse_pool, pool_type_of, stat_pool
from ..gacha.simulate import do_gacha, toggle_bing
from ..render import render_gacha_detail, render_gacha_stat, render_gacha_trial
from .common import _delete_tmp_after, game_of, guard, resolve_uid, send_image

# ---------------------------------------------------------------------------
# 正则（导出常量以便测试交叉命中）
# ---------------------------------------------------------------------------

RE_AUTHKEY = r"^/[\s\S]*authkey="
RE_UPDATE = r"^/(星铁)?更新(抽卡|抽奖|祈愿|跃迁)?记录$"
RE_ANALYSE = r"^/(星铁)?(抽卡|抽奖|角色|武器|光锥|常驻|集录|[uU][pP])+池?(记录|祈愿|分析)$"
RE_STAT = r"^/(星铁)?(全部|抽卡|抽奖|角色|武器|光锥|常驻|集录|[uU][pP]|版本)+池?统计$"
RE_SIMULATE = r"^[#/]?(10|[武器池常驻]*[十]+|抽|单)[连抽卡奖][123武器池常驻]*$"
RE_BING = r"^/定轨$"
RE_IMPORT = r"^/(星铁)?导入记录"
RE_EXPORT = r"^/(星铁)?导出记录$"

authkey_m = on_regex(RE_AUTHKEY, priority=15, block=True)
update_m = on_regex(RE_UPDATE, priority=5, block=True)
analyse_m = on_regex(RE_ANALYSE, priority=5, block=True)
stat_m = on_regex(RE_STAT, priority=5, block=True)
simulate_m = on_regex(RE_SIMULATE, priority=5, block=True)
bing_m = on_regex(RE_BING, priority=5, block=True)
import_m = on_regex(RE_IMPORT, priority=5, block=True)
export_m = on_regex(RE_EXPORT, priority=5, block=True)

# 卡池渲染标题
_POOL_LABELS = {
    "gs": {100: "新手祈愿", 200: "常驻祈愿", 301: "角色活动祈愿", 302: "武器活动祈愿", 500: "集录祈愿"},
    "sr": {
        1: "常驻跃迁", 2: "新手跃迁", 11: "角色活动跃迁",
        12: "光锥活动跃迁", 21: "角色联动跃迁", 22: "光锥联动跃迁",
    },
}

# 关键词组合（如 "角色up"）取第一个可识别 token，优先级从高到低
_KW_TOKENS = ["武器", "光锥", "常驻", "集录", "角色", "抽卡", "抽奖", "全部", "版本", "up", "UP"]


def pool_label_of(keyword: str, game: str) -> str | None:
    """卡池关键词 → 渲染标题（如 "角色活动祈愿"），无法识别返回 None"""
    pool_type = pool_type_of(keyword, game)
    if pool_type is None:
        return None
    return _POOL_LABELS[game].get(pool_type, f"{GACHA_TYPES[game].get(pool_type, '未知')}池")


def _first_token(text: str) -> str:
    for tok in _KW_TOKENS:
        if tok in text:
            return tok
    return text


def analyse_keyword(text: str, game: str) -> str | None:
    """从分析指令文本中提取归一化的卡池关键词，无法识别返回 None"""
    t = text.lstrip("#/").strip()
    if t.startswith("星铁"):
        t = t[2:]
    t = re.sub(r"(记录|祈愿|分析)$", "", t).rstrip("池").strip()
    kw = _first_token(t)
    if pool_type_of(kw, game) is not None:
        return kw
    # 星铁没有 "抽卡/抽奖" 关键词，归入角色池
    if kw in ("抽卡", "抽奖") and pool_type_of("角色", game) is not None:
        return "角色"
    return None


def stat_keyword(text: str) -> str:
    """从统计指令文本中提取归一化的统计关键词（抽卡/抽奖/版本 → 全部）"""
    t = text.lstrip("#/").strip()
    if t.startswith("星铁"):
        t = t[2:]
    t = re.sub(r"统计$", "", t).rstrip("池").strip()
    kw = _first_token(t)
    if kw in ("抽卡", "抽奖", "版本"):
        return "全部"
    return kw


def sim_kind_of(text: str) -> str:
    """模拟抽卡文本 → do_gacha 的 kind"""
    t = text.lstrip("#/")
    if "武器" in t:
        return "weapon"
    if "常驻" in t:
        return "permanent"
    if "2" in t:
        return "role2"
    return "role"


def is_single(text: str) -> bool:
    """是否单抽（不含 十/10），simulate 层只跑十连，单抽截取结果第 1 个"""
    t = text.lstrip("#/")
    return "十" not in t and "10" not in t


def pool_new_summary(result: dict[int, int], game: str) -> str:
    """{gacha_type: 新增条数} → 各池汇总文案"""
    names = GACHA_TYPES[game]
    lines = []
    for gacha_type, num in result.items():
        lines.append(f"{names.get(gacha_type, gacha_type)}池新增 {num} 条")
    if not lines or sum(result.values()) == 0:
        return "没有新增记录（本地已是最新）"
    return "\n".join(lines)


def _scope_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"{event.group_id}:{event.user_id}"
    return f"private:{event.user_id}"


def _sender_name(event: MessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


def _at_msg(event: MessageEvent, text: str) -> Message:
    """群聊带 @，私聊纯文本"""
    if isinstance(event, GroupMessageEvent):
        return MessageSegment.at(event.get_user_id()) + MessageSegment.text(f" {text}")
    return Message(text)


async def _do_update(matcher, event: MessageEvent, authkey_info: dict, game: str, uid: str) -> None:
    """增量更新 + 汇总回复（authkey 嗅探与 /更新抽卡记录 共用）"""
    await matcher.send(f"开始更新抽卡记录（UID {uid}），请稍候...")
    result = await update_gacha_log(event.get_user_id(), uid, authkey_info)
    save_authkey(event.get_user_id(), game, authkey_info)
    await matcher.finish("抽卡记录更新完成\n" + pool_new_summary(result, game))


# ---------------------------------------------------------------------------
# authkey 嗅探：用户直接把游戏里复制的抽卡链接发到群里/私聊
# ---------------------------------------------------------------------------


@authkey_m.handle()
@guard(authkey_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    authkey_info = parse_authkey_url(text)  # 链接不完整抛 GachaLogError，guard 回提示
    game = authkey_info["game"]
    user_id = event.get_user_id()

    uid = store.get_uid(user_id, game)
    if not uid:
        # 未绑定：从抓取结果里拿 uid 并自动绑定
        logs = await fetch_gacha_log(authkey_info, DEFAULT_POOLS[game][0])
        uid = str(logs[0].get("uid")) if logs else None
        if not uid:
            await authkey_m.finish("未能从链接中获取到 UID，请先发送 /绑定uid <uid> 后再更新")
        try:
            store.bind_uid(user_id, game, uid)
        except ValueError:
            pass
    await _do_update(authkey_m, event, authkey_info, game, uid)


# ---------------------------------------------------------------------------
# /更新抽卡记录 / /星铁更新抽卡记录
# ---------------------------------------------------------------------------


@update_m.handle()
@guard(update_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    authkey_info = load_authkey(event.get_user_id(), game)
    if not authkey_info:
        await update_m.finish(
            "未找到可用的抽卡链接（缓存 24 小时内有效）\n"
            "请在游戏中打开抽卡记录页面复制链接，并在链接前加 / 发送给我，或使用 /导入记录 导入 UIGF 文件"
        )
    uid = store.get_uid(event.get_user_id(), game)
    if not uid:
        await update_m.finish("你还未绑定 UID，请先发送 /" + ("星铁" if game == "sr" else "") + "绑定uid <uid>")
    await _do_update(update_m, event, authkey_info, game, uid)


# ---------------------------------------------------------------------------
# 记录分析：/(星铁)?(抽卡|角色|武器|...)(记录|祈愿|分析)
# ---------------------------------------------------------------------------


@analyse_m.handle()
@guard(analyse_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    keyword = analyse_keyword(text, game)
    if keyword is None:
        await analyse_m.finish("无法识别的卡池，支持：角色/武器/光锥/常驻/集录/抽卡")
    uid = resolve_uid(event, text, game)
    if not uid:
        await analyse_m.finish("你还未绑定 UID，请先发送 /" + ("星铁" if game == "sr" else "") + "绑定uid <uid>")
    data = analyse_pool(event.get_user_id(), uid, keyword, game)
    if data is None:
        update_command = "/" + ("星铁" if game == "sr" else "") + "更新抽卡记录"
        await analyse_m.finish(f"暂无抽卡记录，请发送以 / 开头的抽卡链接或 {update_command}")
    png = await render_gacha_detail(data, uid, game, pool_label_of(keyword, game) or "抽卡记录")
    await send_image(analyse_m, png)
    await analyse_m.finish()


# ---------------------------------------------------------------------------
# 按版本统计：/(星铁)?(全部|角色|...|版本)统计
# ---------------------------------------------------------------------------


@stat_m.handle()
@guard(stat_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    keyword = stat_keyword(text)
    uid = resolve_uid(event, text, game)
    if not uid:
        await stat_m.finish("你还未绑定 UID，请先发送 /" + ("星铁" if game == "sr" else "") + "绑定uid <uid>")
    data = stat_pool(event.get_user_id(), uid, keyword, game)
    if data is None:
        update_command = "/" + ("星铁" if game == "sr" else "") + "更新抽卡记录"
        await stat_m.finish(f"暂无抽卡记录，请发送以 / 开头的抽卡链接或 {update_command}")
    png = await render_gacha_stat(data, uid, game)
    await send_image(stat_m, png)
    await stat_m.finish()


# ---------------------------------------------------------------------------
# 模拟抽卡：十连 / 十连2 / 武器十连 / 常驻十连 / 单抽 等（无需前缀）
# ---------------------------------------------------------------------------


@simulate_m.handle()
@guard(simulate_m)
async def _(bot: Bot, event: MessageEvent):
    text = event.get_plaintext()
    kind = sim_kind_of(text)
    is_master = await SUPERUSER(bot, event)
    daily_limit = get_plugin_config(Config).miao_gacha_daily_limit
    result = do_gacha(_scope_key(event), kind, is_master=is_master, daily_limit=daily_limit)
    name = _sender_name(event)
    if result["code"] == "limit":
        await simulate_m.finish(_at_msg(event, f"{name}\n{result['msg']}"))
    if is_single(text):
        # simulate 层只跑十连，单抽截取结果第 1 个展示
        result["list"] = result["list"][:1]
    png = await render_gacha_trial(result, name)
    await send_image(simulate_m, png)
    await simulate_m.finish()


# ---------------------------------------------------------------------------
# /定轨：武器池定轨切换
# ---------------------------------------------------------------------------


@bing_m.handle()
@guard(bing_m)
async def _(event: MessageEvent):
    msg = toggle_bing(_scope_key(event))
    await bing_m.finish(_at_msg(event, msg.strip()))


# ---------------------------------------------------------------------------
# /导入记录：参数为 UIGF json 文件链接或直接跟 JSON 文本
# ---------------------------------------------------------------------------


@import_m.handle()
@guard(import_m)
async def _(event: MessageEvent):
    text = event.get_plaintext()
    param = re.sub(r"^/(星铁)?导入记录", "", text).strip()
    if not param:
        await import_m.finish("用法：/导入记录 <UIGF文件链接> 或 /导入记录 <UIGF的JSON文本>")
    if param.startswith("http://") or param.startswith("https://"):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(param)
                uigf_dict = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            await import_m.finish(f"下载/解析记录文件失败：{e}")
    else:
        try:
            uigf_dict = json.loads(param)
        except ValueError:
            await import_m.finish("JSON 解析失败，请确认发送的是完整的 UIGF 文件内容或下载链接")
    result = import_uigf(event.get_user_id(), uigf_dict)  # 格式错误抛 GachaLogError，guard 回提示
    game = "sr" if (uigf_dict.get("info", {}).get("srgf_version") or "hkrpg" in uigf_dict) else "gs"
    await import_m.finish("抽卡记录导入完成\n" + pool_new_summary(result, game))


# ---------------------------------------------------------------------------
# /导出记录 / /星铁导出记录：UIGF json 文件上传，失败降级为文本
# ---------------------------------------------------------------------------


@export_m.handle()
@guard(export_m)
async def _(bot: Bot, event: MessageEvent):
    text = event.get_plaintext()
    game = game_of(text)
    uid = resolve_uid(event, "", game)
    if not uid:
        await export_m.finish("你还未绑定 UID，请先发送 /" + ("星铁" if game == "sr" else "") + "绑定uid <uid>")
    data = export_uigf(event.get_user_id(), uid, game)
    if not data["list"]:
        await export_m.finish("暂无抽卡记录可导出，请先更新抽卡记录")
    payload = json.dumps(data, ensure_ascii=False)

    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
    safe_path = tmp_path.replace("\\", "/")
    file_name = f"uigf_{game}_{uid}.json"

    uploaded = False
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api(
                "upload_group_file", group_id=event.group_id, file=f"file:///{safe_path}", name=file_name
            )
        else:
            await bot.call_api(
                "upload_private_file", user_id=event.user_id, file=f"file:///{safe_path}", name=file_name
            )
        uploaded = True
    except Exception as e:
        logger.warning(f"[miao] 记录文件上传失败，降级为文本发送: {e}")

    if uploaded:
        _ = asyncio.create_task(_delete_tmp_after(tmp_path, 60))
        await export_m.finish(f"抽卡记录已导出（UIGF，UID {uid}），共 {len(data['list'])} 条")
    # 降级：直接发 JSON 文本（截断）
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    truncated = payload[:3000] + ("\n...（内容过长已截断）" if len(payload) > 3000 else "")
    await export_m.finish("文件上传失败，改为文本发送：\n" + truncated)
