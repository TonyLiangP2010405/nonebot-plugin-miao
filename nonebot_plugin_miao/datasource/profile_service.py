"""面板更新服务：CD 控制 + 数据源抓取/解析 + 合并落盘

移植 refs/miao-plugin models/serv/ProfileReq.js（请求/CD）与 Serv.js（数据源选择，
本项目固定 gs=enka、sr=mihomo）的核心流程。
"""
from __future__ import annotations

from typing import Any

from ..core import store
from ..core.player import Player
from .enka import fetch_enka, parse_enka
from .errors import ProfileError  # noqa: F401  （对外 re-export，调用方从此处取异常）
from .mihomo import fetch_mihomo, parse_mihomo

# game → (抓取函数, 解析函数)
_FETCH_PARSE = {
    "gs": (fetch_enka, parse_enka),
    "sr": (fetch_mihomo, parse_mihomo),
}


def _interval_seconds() -> int:
    """面板更新冷却秒数（config.miao_profile_interval 分钟），nonebot 未初始化时用默认 3 分钟"""
    try:
        from nonebot import get_plugin_config

        from ..config import Config

        return int(get_plugin_config(Config).miao_profile_interval) * 60
    except Exception:
        return 3 * 60


async def update_profile(user_id: str | int, uid: str | int, game: str) -> dict[str, Any]:
    """更新面板：CD 检查 → 抓取 → 解析 → 合并落盘 → 写 CD

    返回：
      - {"code": "cd", "wait": 剩余秒数}：冷却中，未发起请求
      - {"code": "ok", "player": Player, "new_chars": [本次更新的角色名]}
    数据源错误抛 ProfileError（中文消息，命令层直接提示）。
    """
    store._check_game(game)
    uid = str(uid)
    cd_key = f"profile:{game}:{uid}"
    wait = store.check_cd(cd_key, _interval_seconds())
    if wait > 0:
        return {"code": "cd", "wait": wait}
    fetch, parse = _FETCH_PARSE[game]
    raw = await fetch(uid)
    parsed = parse(raw, uid)
    player = Player.load(uid, game)
    player.update(parsed)
    player.save()
    # CD 取配置间隔与数据源 ttl 的较大值（对齐 miao ProfileServ.getCdTime）
    store.set_cd(cd_key, max(_interval_seconds(), int(parsed.get("ttl") or 0)))
    new_chars = [avatar.get("name") or aid for aid, avatar in (parsed.get("avatars") or {}).items()]
    return {"code": "ok", "player": player, "new_chars": new_chars}
