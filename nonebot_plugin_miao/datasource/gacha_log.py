"""抽卡记录获取流水线：authkey 链接解析 + getGachaLog 增量抓取 + authkey 缓存

接口与行为移植自 Yunzai-genshin model/gachaLog.js，HTTP 客户端换为 httpx.AsyncClient。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from nonebot_plugin_miao.core import store

# 抽卡记录 API 域名：国服走 mihoyo.com，国际服走 hoyoverse.com
_GS_API_CN = "https://public-operation-hk4e.mihoyo.com/gacha_info/api/getGachaLog"
_GS_API_OS = "https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/getGachaLog"
_SR_API_CN = "https://public-operation-hkrpg.mihoyo.com/common/gacha_record/api/getGachaLog"
_SR_API_OS = "https://public-operation-hkrpg-sg.hoyoverse.com/common/gacha_record/api/getGachaLog"
# 星铁联动池专用端点（仅路径不同，域名规则相同）
_SR_LD_API_CN = "https://public-operation-hkrpg.mihoyo.com/common/gacha_record/api/getLdGachaLog"
_SR_LD_API_OS = "https://public-operation-hkrpg-sg.hoyoverse.com/common/gacha_record/api/getLdGachaLog"

# gacha_type 映射
GACHA_TYPES: dict[str, dict[int, str]] = {
    "gs": {100: "新手", 200: "常驻", 301: "角色", 302: "武器", 500: "集录"},
    "sr": {1: "常驻", 2: "新手", 11: "角色", 12: "光锥", 21: "角色联动", 22: "光锥联动"},
}

# 默认更新的卡池（与 Yunzai 参考实现保持一致，原神不含新手池）
DEFAULT_POOLS: dict[str, list[int]] = {
    "gs": [301, 302, 500, 200],
    "sr": [11, 12, 21, 22, 1, 2],
}

# 国服 region 集合，其余一律走国际服域名
_CN_REGIONS = {"gs": {"cn_gf01", "cn_qd01"}, "sr": {"prod_gf_cn", "prod_qd_cn"}}

# 星铁联动池 gacha_type（走 getLdGachaLog 端点）
_SR_LD_TYPES = {21, 22}

# authkey 缓存有效期（秒），与 Yunzai redis 86400 一致
AUTHKEY_TTL = 86400

_PAGE_SIZE = 20
_PAGE_SLEEP = 0.3
_POOL_SLEEP = 0.5
_TIMEOUT = 30

# 星铁链接特征：/common/ 或 /hkrpg/ 路径（同参考实现 isSr 判定）
_SR_URL_RE = re.compile(r"/(common|hkrpg)/")

# 错误码文案（参考 gcLog.js / gachaLog.js checkUrl）
_ERROR_MSGS = {
    -101: "authkey 已过期，请重新进入游戏，重新复制链接",
    -100: "链接不完整，请长按全选复制全部内容（可能输入法复制限制），或者复制的不是历史记录页面链接",
    -109: "2.3版本后，反馈的链接已无法查询！请用安卓方式获取链接",
}


class GachaLogError(Exception):
    """抽卡记录获取失败（链接无效、authkey 过期、API 返回错误等）"""

    def __init__(self, message: str, retcode: int | None = None):
        super().__init__(message)
        self.retcode = retcode


def is_cn_region(region: str, game: str) -> bool:
    """region 是否国服（决定走 mihoyo.com 还是 hoyoverse.com 域名）"""
    return region in _CN_REGIONS[game]


def region_from_uid(uid: str | int, game: str) -> str:
    """按 UID 推断服务器 region（移植自 gachaLog.js getServer）

    取 uid 去掉末 8 位后的前缀：1/2→官服，5→渠道服(B服)，6→美服，7→欧服，8/18→亚服，9→港澳台服
    """
    store._check_game(game)
    prefix = str(uid)[:-8]
    table = {
        "gs": {
            "1": "cn_gf01", "2": "cn_gf01", "5": "cn_qd01",
            "6": "os_usa", "7": "os_euro", "8": "os_asia", "18": "os_asia", "9": "os_cht",
        },
        "sr": {
            "1": "prod_gf_cn", "2": "prod_gf_cn", "5": "prod_qd_cn",
            "6": "prod_official_usa", "7": "prod_official_euro",
            "8": "prod_official_asia", "18": "prod_official_asia", "9": "prod_official_cht",
        },
    }
    return table[game].get(prefix, table[game]["1"])


def _api_url(game: str, region: str, gacha_type: int) -> str:
    """按游戏/区服/卡池选择 API 端点"""
    cn = is_cn_region(region, game)
    if game == "gs":
        return _GS_API_CN if cn else _GS_API_OS
    if gacha_type in _SR_LD_TYPES:
        return _SR_LD_API_CN if cn else _SR_LD_API_OS
    return _SR_API_CN if cn else _SR_API_OS


def parse_authkey_url(text: str) -> dict[str, Any]:
    """从用户发送的文本中解析含 authkey 的抽卡链接

    链接可能混在长文本里，authkey 可能带 URL 编码（%2F 等，parse_qsl 会自动 unquote）。
    返回 {"authkey": str, "region": str | None, "game": "gs" | "sr"}；
    region 缺失时返回 None，由调用方按 UID 推断。解析失败抛 GachaLogError。
    """
    text = (text or "").replace("〈=", "&")  # 修复链接里的奇怪符号（同参考实现 dealUrl）
    query = ""
    for marker in ("getLdGachaLog?", "getGachaLog?", "index.html?"):
        if marker in text:
            query = text.split(marker, 1)[1]
            break
    else:
        # 长文本中提取含 authkey 的片段
        m = re.search(r"[^\s]*authkey=[^\s]*", text)
        if m:
            query = m.group(0)
    # 只取第一个空白前的 token，剥掉可能的 URL 前缀，避免把后面的闲聊文本当参数
    query = query.split()[0] if query.split() else ""
    if "?" in query:
        query = query.split("?", 1)[1]
    if query.startswith("&"):
        query = query[1:]

    params = dict(httpx.QueryParams(query)) if query else {}
    authkey = params.get("authkey", "")
    # 去除 APP 分享链接末尾的 #/、#/log 锚点
    authkey = re.sub(r"#/(log)?$", "", authkey)
    if not authkey:
        raise GachaLogError(
            "链接不完整，请长按全选复制全部内容（可能输入法复制限制），或者复制的不是历史记录页面链接",
            retcode=-100,
        )

    game = "sr" if _SR_URL_RE.search(text) or params.get("game_biz", "").startswith("hkrpg") else "gs"
    return {"authkey": authkey, "region": params.get("region") or None, "game": game}


def _resolve_region(authkey_info: dict, uid: str | int | None = None) -> str:
    """确定请求用 region：优先链接自带，其次按 UID 推断，默认国服官服"""
    if authkey_info.get("region"):
        return str(authkey_info["region"])
    game = authkey_info["game"]
    if uid:
        return region_from_uid(uid, game)
    return "prod_gf_cn" if game == "sr" else "cn_gf01"


def _check_retcode(res: dict) -> None:
    """API 返回 retcode != 0 时抛带中文提示的 GachaLogError"""
    retcode = res.get("retcode", 0)
    if retcode == 0:
        return
    msg = _ERROR_MSGS.get(retcode) or f"获取抽卡记录失败：{res.get('message', '未知错误')}（retcode={retcode}）"
    raise GachaLogError(msg, retcode=retcode)


async def _request_page(
    client: httpx.AsyncClient,
    authkey_info: dict,
    gacha_type: int,
    page: int,
    end_id: str,
    uid: str | int | None = None,
) -> list[dict]:
    """请求单页记录，返回 list（空列表表示没有更多）"""
    game = authkey_info["game"]
    region = _resolve_region(authkey_info, uid)
    params: dict[str, Any] = {
        "authkey_ver": 1,
        "lang": "zh-cn",
        "gacha_type": gacha_type,
        "page": page,
        "size": _PAGE_SIZE,
        "end_id": end_id,
        "authkey": authkey_info["authkey"],
        "region": region,
    }
    if game == "sr":
        params["game_biz"] = "hkrpg_cn" if is_cn_region(region, game) else "hkrpg_global"
    url = _api_url(game, region, int(gacha_type))
    try:
        resp = await client.get(url, params=params)
        res = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise GachaLogError(f"获取抽卡记录失败：网络错误（{e}）") from e
    _check_retcode(res)
    data = res.get("data") or {}
    return data.get("list") or []


async def fetch_gacha_log(
    authkey_info: dict,
    gacha_type: int,
    uid: str | int | None = None,
) -> list[dict]:
    """分页抓取单个卡池的全部记录（API 原始字段 dict 列表，最新在前）

    page 从 1 递增，size=20，每页最后一条 id 作为下一页 end_id，
    直到返回空页或不足 20 条；页间 sleep 0.3 防止触发限流。
    """
    all_logs: list[dict] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        page = 1
        end_id = "0"
        while True:
            logs = await _request_page(client, authkey_info, int(gacha_type), page, end_id, uid)
            if not logs:
                break
            all_logs.extend(logs)
            if len(logs) < _PAGE_SIZE:
                break
            end_id = str(logs[-1]["id"])
            page += 1
            await asyncio.sleep(_PAGE_SLEEP)
    return all_logs


def _merge_logs(new_logs: list[dict], local_logs: list[dict]) -> list[dict]:
    """新记录与本地记录合并去重（按 id），按 id 降序排列（最新在前）

    id 为数值字符串，直接 int 排序；个别 UIGF 导入的超长/异常 id 退化为字符串排序
    """

    def _key(item: dict) -> tuple[int, Any]:
        sid = str(item.get("id", ""))
        return (0, int(sid)) if sid.isdigit() else (1, sid)

    seen: set[str] = set()
    merged: list[dict] = []
    for item in list(new_logs) + list(local_logs):
        sid = str(item.get("id", ""))
        if sid in seen:
            continue
        seen.add(sid)
        merged.append(item)
    merged.sort(key=_key, reverse=True)
    return merged


async def update_gacha_log(
    user_id: str | int,
    uid: str | int,
    authkey_info: dict,
    pool_types: list[int] | None = None,
) -> dict[int, int]:
    """增量更新用户各卡池抽卡记录，返回 {gacha_type: 新增条数}

    翻页时若整页记录 id 全部已存在则提前停止；新记录与本地合并去重（按 id）、
    按 id 排序后经 store.write_gacha_log 落盘；池间 sleep 0.5。
    pool_types 默认该游戏全部卡池（原神 301/302/500/200，星铁 11/12/21/22/1/2）。
    """
    game = authkey_info["game"]
    store._check_game(game)
    if pool_types is None:
        pool_types = DEFAULT_POOLS[game]

    result: dict[int, int] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i, gacha_type in enumerate(pool_types):
            local_logs = store.read_gacha_log(user_id, uid, gacha_type, game)
            local_ids = {str(item.get("id", "")) for item in local_logs}

            new_logs: list[dict] = []
            page = 1
            end_id = "0"
            while True:
                logs = await _request_page(client, authkey_info, gacha_type, page, end_id, uid)
                if not logs:
                    break
                page_ids = {str(item.get("id", "")) for item in logs}
                if page_ids and page_ids <= local_ids:
                    # 整页记录本地都已存在，更早的记录无需再翻
                    break
                new_logs.extend(item for item in logs if str(item.get("id", "")) not in local_ids)
                if len(logs) < _PAGE_SIZE:
                    break
                end_id = str(logs[-1]["id"])
                page += 1
                await asyncio.sleep(_PAGE_SLEEP)

            if new_logs:
                merged = _merge_logs(new_logs, local_logs)
                store.write_gacha_log(user_id, uid, gacha_type, game, merged)
            result[gacha_type] = len(new_logs)

            if i < len(pool_types) - 1:
                await asyncio.sleep(_POOL_SLEEP)
    return result


# ---------------- authkey 缓存（data/authkeys.json，24h 过期） ----------------


def _authkeys_path():
    return store._data_dir() / "authkeys.json"


def save_authkey(user_id: str | int, game: str, authkey_info: dict) -> None:
    """缓存用户的 authkey 信息（带保存时间戳，24h 有效）"""
    store._check_game(game)
    data = store.load_json(_authkeys_path(), {}) or {}
    data.setdefault(str(user_id), {})[game] = {
        "authkey": authkey_info["authkey"],
        "region": authkey_info.get("region"),
        "game": game,
        "time": int(time.time()),
    }
    store.save_json(_authkeys_path(), data)


def load_authkey(user_id: str | int, game: str) -> dict | None:
    """读取缓存的 authkey，不存在或超过 24h 返回 None"""
    store._check_game(game)
    data = store.load_json(_authkeys_path(), {}) or {}
    entry = data.get(str(user_id), {}).get(game)
    if not entry:
        return None
    if int(time.time()) - int(entry.get("time", 0)) > AUTHKEY_TTL:
        return None
    return {"authkey": entry["authkey"], "region": entry.get("region"), "game": game}
