"""数据存储层：UID 绑定、米游社 cookie、冷却、抽卡记录、面板数据、模拟抽卡状态

全部基于 nonebot_plugin_localstore 的数据目录 + JSON 文件，纯函数实现，
线程安全用一把模块级 threading.Lock 即可（文件级读写，无数据库）。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# 游戏标识：gs=原神，sr=星穹铁道
GAMES = ("gs", "sr")

# UID 校验：9-10 位数字，18 开头允许到 11 位（与 miao-plugin 保持一致）
_UID_RE = re.compile(r"^(18|[1-9])\d{8,9}$")

_LOCK = threading.Lock()


def _data_dir() -> Path:
    """插件持久化数据目录（测试中可 monkeypatch 到 tmp_path）

    nonebot_plugin_localstore 在 import 时就要求 nonebot 已初始化，故延迟到调用时导入
    """
    from nonebot_plugin_localstore import get_plugin_data_dir

    return Path(get_plugin_data_dir())


def _cache_dir() -> Path:
    """插件缓存目录"""
    from nonebot_plugin_localstore import get_plugin_cache_dir

    return Path(get_plugin_cache_dir())


def _check_game(game: str) -> None:
    if game not in GAMES:
        raise ValueError(f"非法游戏标识: {game!r}，仅支持 {GAMES}")


def load_json(path: Path, default: Any = None) -> Any:
    """读取 JSON 文件，不存在或解析失败时返回 default"""
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, obj: Any) -> None:
    """原子写 JSON 文件：先写临时文件再 rename，避免写一半损坏数据"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp_name, path)
    except BaseException:
        # 写失败时清理临时文件
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------- UID 绑定（data/bind.json：{game: {user_id: uid}}） ----------------


def bind_uid(user_id: str | int, game: str, uid: str | int) -> str:
    """绑定 UID，返回规范化后的 uid 字符串；uid 不合法时抛 ValueError"""
    _check_game(game)
    uid = str(uid).strip()
    if not _UID_RE.match(uid):
        raise ValueError(f"非法 UID: {uid!r}")
    user_id = str(user_id)
    with _LOCK:
        binds = load_json(_data_dir() / "bind.json", {}) or {}
        binds.setdefault(game, {})[user_id] = uid
        save_json(_data_dir() / "bind.json", binds)
    return uid


def get_uid(user_id: str | int, game: str) -> str | None:
    """查询用户绑定的 UID，未绑定返回 None"""
    _check_game(game)
    binds = load_json(_data_dir() / "bind.json", {}) or {}
    return binds.get(game, {}).get(str(user_id))


def del_bind(user_id: str | int, game: str | None = None) -> bool:
    """解绑 UID。game 为 None 时解绑该用户所有游戏，返回是否有改动"""
    user_id = str(user_id)
    games = [game] if game else list(GAMES)
    for g in games:
        _check_game(g)
    with _LOCK:
        binds = load_json(_data_dir() / "bind.json", {}) or {}
        changed = False
        for g in games:
            if binds.get(g, {}).pop(user_id, None) is not None:
                changed = True
        if changed:
            save_json(_data_dir() / "bind.json", binds)
    return changed


# ---------------- 米游社 cookie（data/cookies.json：{user_id: cookie}，敏感信息） ----------------


def set_cookie(user_id: str | int, cookie: str) -> None:
    """保存用户的米游社 cookie（敏感信息，仅存本地数据目录）"""
    user_id = str(user_id)
    with _LOCK:
        cookies = load_json(_data_dir() / "cookies.json", {}) or {}
        cookies[user_id] = cookie
        save_json(_data_dir() / "cookies.json", cookies)


def get_cookie(user_id: str | int) -> str | None:
    """读取用户的米游社 cookie，未设置返回 None"""
    cookies = load_json(_data_dir() / "cookies.json", {}) or {}
    return cookies.get(str(user_id))


def del_cookie(user_id: str | int) -> bool:
    """删除用户的米游社 cookie，返回是否有改动"""
    user_id = str(user_id)
    with _LOCK:
        cookies = load_json(_data_dir() / "cookies.json", {}) or {}
        if cookies.pop(user_id, None) is None:
            return False
        save_json(_data_dir() / "cookies.json", cookies)
    return True


# ---------------- 冷却（data/cd.json：{key: 到期时间戳}） ----------------


def check_cd(key: str, seconds: int) -> int:
    """检查冷却，返回剩余秒数（0 表示可用）。seconds 仅用于兼容调用方语义"""
    cd_data = load_json(_data_dir() / "cd.json", {}) or {}
    expire = cd_data.get(key, 0)
    remaining = expire - time.time()
    return max(0, math.ceil(remaining))


def set_cd(key: str, seconds: int) -> None:
    """设置冷却，seconds 秒后可用"""
    with _LOCK:
        cd_data = load_json(_data_dir() / "cd.json", {}) or {}
        # 顺手清理已过期的 key，避免文件无限膨胀
        now = time.time()
        cd_data = {k: v for k, v in cd_data.items() if v > now}
        cd_data[key] = now + seconds
        save_json(_data_dir() / "cd.json", cd_data)


# ---------------- 抽卡记录（data/gacha/{game}/{user_id}/{uid}/{gacha_type}.json） ----------------


def gacha_log_path(user_id: str | int, uid: str | int, gacha_type: str | int, game: str) -> Path:
    """抽卡记录文件路径（自动创建父目录）"""
    _check_game(game)
    path = _data_dir() / "gacha" / game / str(user_id) / str(uid) / f"{gacha_type}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_gacha_log(user_id: str | int, uid: str | int, gacha_type: str | int, game: str) -> list:
    """读取抽卡记录（JSON 数组），不存在返回空列表"""
    data = load_json(gacha_log_path(user_id, uid, gacha_type, game), [])
    return data if isinstance(data, list) else []


def write_gacha_log(user_id: str | int, uid: str | int, gacha_type: str | int, game: str, logs: list) -> None:
    """覆盖写入抽卡记录（JSON 数组）"""
    save_json(gacha_log_path(user_id, uid, gacha_type, game), logs)


# ---------------- 面板数据（data/player/{game}/{uid}.json） ----------------


def player_path(game: str, uid: str | int) -> Path:
    """玩家面板数据文件路径（自动创建父目录）"""
    _check_game(game)
    path = _data_dir() / "player" / game / f"{uid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_player(game: str, uid: str | int) -> dict:
    """读取玩家面板数据，不存在返回空 dict"""
    data = load_json(player_path(game, uid), {})
    return data if isinstance(data, dict) else {}


def write_player(game: str, uid: str | int, data: dict) -> None:
    """覆盖写入玩家面板数据"""
    save_json(player_path(game, uid), data)


# ---------------- 模拟抽卡用户状态（data/sim/{scope_key}.json） ----------------


def sim_state_path(scope_key: str) -> Path:
    """模拟抽卡状态文件路径，scope_key 形如 "group_123:456" 或 "private:456" """
    path = _data_dir() / "sim" / f"{scope_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_sim_state(scope_key: str) -> dict:
    """读取模拟抽卡状态，不存在返回空 dict"""
    data = load_json(sim_state_path(scope_key), {})
    return data if isinstance(data, dict) else {}


def write_sim_state(scope_key: str, state: dict) -> None:
    """覆盖写入模拟抽卡状态"""
    save_json(sim_state_path(scope_key), state)
