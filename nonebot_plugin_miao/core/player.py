"""玩家面板数据模型：Player 类的加载/保存/角色查询/合并更新

数据结构与 miao-plugin models/Player.js + Avatar.js 落盘格式对齐，
存储由 core.store 的 read_player/write_player 落地（data/player/{game}/{uid}.json）。
"""
from __future__ import annotations

from typing import Any

from . import meta, store

# 玩家基础信息字段（对齐 miao Player.js setBasicData/save 的 key）
_BASIC_KEYS = ("name", "level", "word", "face", "card", "sign")


class Player:
    """单个 UID 的面板数据（game=gs 原神 / sr 星铁）

    avatars 为 {str(角色id): avatar dict}，avatar 结构见 datasource/enka.py、
    datasource/mihomo.py 的模块说明（与 miao 统一 avatar 结构一致）。
    """

    def __init__(self, uid: str | int, game: str = "gs", data: dict[str, Any] | None = None):
        store._check_game(game)
        self.uid = str(uid)
        self.game = game
        data = data or {}
        for key in _BASIC_KEYS:
            setattr(self, key, data.get(key) or "")
        self.data_source: str = data.get("dataSource") or ""
        self.update_time: int = data.get("updateTime") or 0
        self.avatars: dict[str, dict[str, Any]] = dict(data.get("avatars") or {})

    # ---------------- 存取 ----------------

    @classmethod
    def load(cls, uid: str | int, game: str = "gs") -> Player:
        """从本地数据目录加载，不存在时返回空 Player"""
        return cls(uid, game, store.read_player(game, uid))

    def save(self) -> None:
        """落盘到 data/player/{game}/{uid}.json"""
        store.write_player(self.game, self.uid, self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            **{key: getattr(self, key) for key in _BASIC_KEYS},
            "avatars": self.avatars,
            "dataSource": self.data_source,
            "updateTime": self.update_time,
        }

    # ---------------- 更新 ----------------

    def update(self, parsed: dict[str, Any]) -> None:
        """合并一次面板解析结果（对齐 miao Player.js 的 setBasicData + setAvatar 语义）

        基础信息整体覆盖；avatar 按角色 id 覆盖更新，本次未涉及的角色保留原数据
        （即使来自其他数据源，各 avatar 自带 _source/_time 标记来源）。
        """
        for key in _BASIC_KEYS:
            if parsed.get(key):
                setattr(self, key, parsed[key])
        self.data_source = parsed.get("dataSource") or self.data_source
        self.update_time = parsed.get("updateTime") or self.update_time
        self.avatars.update(parsed.get("avatars") or {})

    # ---------------- 查询 ----------------

    def get_avatar(self, name_or_alias: str | int) -> dict[str, Any] | None:
        """按 名字/别名/id 取角色面板数据，找不到返回 None（经 meta 别名解析）"""
        char = meta.get_character(name_or_alias, self.game)
        if not char:
            # meta 查不到时，允许直接按已存 id 查
            return self.avatars.get(str(name_or_alias).strip())
        candidates = [str(char.id)]
        # 旅行者（gs）：兄妹两个 id 取存在的那个（对齐 miao Player.js getAvatar）
        if char.name == "旅行者":
            candidates = ["10000005", "10000007"]
        elif self.game == "sr" and 8001 <= int(char.id) <= 8018:
            # 开拓者（sr）：同命运的男女主 id 相邻，取存在的那个
            cid = int(char.id)
            candidates.append(str(cid + 1 if cid % 2 else cid - 1))
        for cid in candidates:
            if cid in self.avatars:
                return self.avatars[cid]
        return None

    def avatar_names(self) -> list[str]:
        """所有已存角色的名字（meta 查不到时回退 id 字符串）"""
        names: list[str] = []
        for aid in self.avatars:
            char = meta.get_character(aid, self.game)
            names.append(char.name if char else aid)
        return names
