"""UIGF 统一可交换祈愿记录标准的导入导出

格式参考 Yunzai-genshin model/exportLog.js：
- 原神 UIGF v2.x / 星铁 SRGF v1.0：{"info": {...}, "list": [...]}
- UIGF v4.0：{"info": {...}, "hk4e"/"hkrpg": [{"uid": ..., "list": [...]}]}
list 项字段：id/gacha_type/uigf_gacha_type/name/time/item_type/rank_type/lang 等
"""
from __future__ import annotations

import time
from typing import Any

from nonebot_plugin_miao.core import store
from nonebot_plugin_miao.datasource.gacha_log import DEFAULT_POOLS, GachaLogError, _merge_logs

_APP_NAME = "nonebot-plugin-miao"


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version(_APP_NAME)
    except Exception:
        return "0.1.0"


def _parse_uigf(uigf_dict: dict) -> tuple[str, str, list[dict]]:
    """解析 UIGF/SRGF/v4 三种结构，返回 (game, uid, list)

    - info.srgf_version 或顶层 hkrpg 键 → 星铁(sr)，其余 → 原神(gs)
    - v4 格式取 hk4e/hkrpg 数组第一个账号的记录
    """
    if not isinstance(uigf_dict, dict):
        raise GachaLogError("json文件内容错误：非统一祈愿记录标准")

    info = uigf_dict.get("info") or {}
    if "list" in uigf_dict:
        game = "sr" if info.get("srgf_version") else "gs"
        uid = str(info.get("uid") or "")
        records = uigf_dict["list"]
    elif "hkrpg" in uigf_dict:
        game, records = "sr", (uigf_dict["hkrpg"] or [{}])[0].get("list") or []
        uid = str((uigf_dict["hkrpg"] or [{}])[0].get("uid") or info.get("uid") or "")
    elif "hk4e" in uigf_dict:
        game, records = "gs", (uigf_dict["hk4e"] or [{}])[0].get("list") or []
        uid = str((uigf_dict["hk4e"] or [{}])[0].get("uid") or info.get("uid") or "")
    else:
        raise GachaLogError("json文件内容错误：非统一祈愿记录标准")

    if not uid:
        raise GachaLogError("json文件内容错误：缺少 uid")
    if not isinstance(records, list) or not records:
        raise GachaLogError("json文件内容错误：记录列表为空")

    # 必要字段校验（同参考实现 dealJson 的 reqField）
    required = ["id", "gacha_type", "item_type", "name", "time"]
    missing = [f for f in required if not records[0].get(f)]
    # v4 记录可没有 name/item_type，只要有 item_id
    if missing and not records[0].get("item_id"):
        raise GachaLogError(f"json文件内容错误：缺少必要字段 {', '.join(missing)}")
    return game, uid, records


def _uigf_gacha_type(item: dict, game: str) -> int:
    """取记录的分组卡池类型：优先 uigf_gacha_type，原神 400 归入 301"""
    value = item.get("uigf_gacha_type") or item.get("gacha_type")
    value = int(value)
    if game == "gs" and value == 400:
        value = 301
    return value


def import_uigf(user_id: str | int, uigf_dict: dict) -> dict[int, int]:
    """导入 UIGF 抽卡记录，按 uigf_gacha_type 分组与本地记录合并去重落盘

    返回 {gacha_type: 新增条数}；格式不合法抛 GachaLogError。
    """
    game, uid, records = _parse_uigf(uigf_dict)

    # 按 uigf_gacha_type 分组
    groups: dict[int, list[dict]] = {}
    for item in records:
        groups.setdefault(_uigf_gacha_type(item, game), []).append(item)

    result: dict[int, int] = {}
    for gacha_type, items in groups.items():
        local_logs = store.read_gacha_log(user_id, uid, gacha_type, game)
        local_ids = {str(v.get("id", "")) for v in local_logs}
        new_logs = [v for v in items if str(v.get("id", "")) not in local_ids]
        if new_logs:
            store.write_gacha_log(user_id, uid, gacha_type, game, _merge_logs(new_logs, local_logs))
        result[gacha_type] = len(new_logs)
    return result


def export_uigf(user_id: str | int, uid: str | int, game: str) -> dict[str, Any]:
    """从本地记录生成标准 UIGF dict

    原神导出 UIGF v2.3（400 归入 301），星铁导出 SRGF v1.0；list 按 id 升序。
    """
    store._check_game(game)
    records: list[dict] = []
    for gacha_type in DEFAULT_POOLS[game]:
        for item in store.read_gacha_log(user_id, uid, gacha_type, game):
            item = dict(item)
            # 原神 UIGF 要求角色活动祈愿 301/400 统一记为 301（同参考实现 getAllList）
            item["uigf_gacha_type"] = str(_uigf_gacha_type(item, game))
            records.append(item)

    def _key(item: dict) -> tuple[int, Any]:
        sid = str(item.get("id", ""))
        return (0, int(sid)) if sid.isdigit() else (1, sid)

    records.sort(key=_key)

    info: dict[str, Any] = {
        "uid": str(uid),
        "lang": str(records[0].get("lang", "zh-cn")) if records else "zh-cn",
        "export_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "export_timestamp": str(int(time.time())),
        "export_app": _APP_NAME,
        "export_app_version": _app_version(),
    }
    if game == "sr":
        info["srgf_version"] = "v1.0"
    else:
        info["uigf_version"] = "v2.3"
    return {"info": info, "list": records}
