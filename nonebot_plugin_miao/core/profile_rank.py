"""群面板排名：按群成员绑定的 UID 读取本地面板，计算评分/伤害并排序。

对齐 miao-plugin 的群排名玩法，但不依赖 Redis：
- 单角色排名：``/优菈面板排名``，按圣遗物评分排序；``/优菈伤害排名`` 按期望伤害排序；
- 群友综合排名：``/面板排名``，每人取自己评分最高的角色参与排序；
- 群成员列表通过 OneBot ``get_group_member_list`` 获取，接口不可用时退回全部绑定用户。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

from . import meta, store
from .artis_mark import calc_mark
from .attr_calc import calc_attr, elem_name
from .player import Player

MODE_MARK = "mark"
MODE_DMG = "dmg"
MODE_NAME = {MODE_MARK: "圣遗物评分", MODE_DMG: "期望伤害"}
RANK_LIMIT = 15


@dataclass
class RankEntry:
    """一条群排名记录。"""

    qq: str
    nickname: str
    uid: str
    avatar: dict[str, Any]
    char_name: str
    level: int
    cons: int
    elem: str
    star: int
    score: float
    mark: float
    mark_class: str | None
    dmg_avg: float | None = None

    @property
    def mode_text(self) -> str:
        return f"{self.score:,.1f}" if self.score >= 1000 else f"{self.score:.1f}"


async def group_members(bot: Bot, group_id: int | str, game: str) -> dict[str, str]:
    """群成员 {QQ: 昵称}；接口失败时退回全部绑定用户。"""
    try:
        raw = await bot.get_group_member_list(group_id=int(group_id))
    except Exception as e:
        logger.warning(f"[miao] 获取群成员列表失败，群排名退回全部绑定用户：{type(e).__name__}: {e}")
        return {qq: qq for qq in store.all_binds(game)}
    members: dict[str, str] = {}
    for item in raw or []:
        qq = str(item.get("user_id") or "").strip()
        if not qq:
            continue
        nickname = str(item.get("card") or item.get("nickname") or qq)
        members[qq] = nickname
    if not members:
        return {qq: qq for qq in store.all_binds(game)}
    return members


def _avatar_meta(avatar: dict[str, Any], game: str) -> tuple[str, str, int]:
    char = meta.get_character(avatar.get("id") or avatar.get("name") or "", game)
    name = char.name if char else str(avatar.get("name") or "未知")
    elem = elem_name(avatar.get("elem") or (char.get("elem") if char else ""), game)
    star = int((char.get("star") if char else 0) or avatar.get("star") or 0)
    return name, elem, star


def _score_avatar(
    avatar: dict[str, Any], game: str, mode: str
) -> tuple[float, float, str | None, float | None]:
    """返回 (排序分, 圣遗物总分, 档位, 伤害期望)。"""
    mark_data = calc_mark(avatar, calc_attr(avatar, game), game)
    mark = float(mark_data.get("mark") or 0)
    mark_class = mark_data.get("markClass")
    dmg_avg: float | None = None
    if mode == MODE_DMG:
        try:
            from ..dmg import calc_dmg

            dmg = calc_dmg(avatar, game)
            selected = dmg.get("selected") or {}
            value = selected.get("avg")
            dmg_avg = float(value) if isinstance(value, (int, float)) else None
        except Exception:
            dmg_avg = None
        return (dmg_avg or 0.0), mark, mark_class, dmg_avg
    return mark, mark, mark_class, None


def _entry(
    qq: str,
    nickname: str,
    uid: str,
    avatar: dict[str, Any],
    game: str,
    mode: str,
) -> RankEntry | None:
    try:
        score, mark, mark_class, dmg_avg = _score_avatar(avatar, game, mode)
    except Exception as e:
        logger.debug(f"[miao] 群排名跳过无法评分的角色：UID {uid} {avatar.get('name')} ({e})")
        return None
    if score <= 0:
        return None
    name, elem, star = _avatar_meta(avatar, game)
    return RankEntry(
        qq=qq,
        nickname=nickname or qq,
        uid=uid,
        avatar=avatar,
        char_name=name,
        level=int(avatar.get("level") or 0),
        cons=int(avatar.get("cons") or 0),
        elem=elem,
        star=star,
        score=score,
        mark=mark,
        mark_class=mark_class,
        dmg_avg=dmg_avg,
    )


async def collect_rank(
    bot: Bot,
    group_id: int | str,
    game: str,
    char_name: str | None = None,
    mode: str = MODE_MARK,
    limit: int = RANK_LIMIT,
) -> list[RankEntry]:
    """收集群排名；char_name 为空时每人取评分最高的角色。"""
    mode = MODE_DMG if mode == MODE_DMG else MODE_MARK
    char = meta.resolve_character(char_name, game) if char_name else None
    members = await group_members(bot, group_id, game)
    entries: list[RankEntry] = []
    for qq, nickname in members.items():
        uid = store.get_uid(qq, game)
        if not uid:
            continue
        player = Player.load(uid, game)
        if not player.avatars:
            continue
        if char is not None:
            avatar = player.get_avatar(char.name)
            if not avatar:
                continue
            entry = _entry(qq, nickname, uid, avatar, game, mode)
            if entry is not None:
                entries.append(entry)
            continue
        # 综合榜：每人只取评分最高的角色
        best: RankEntry | None = None
        for avatar in player.avatars.values():
            if not avatar.get("artis"):
                continue
            entry = _entry(qq, nickname, uid, avatar, game, MODE_MARK)
            if entry is not None and (best is None or entry.score > best.score):
                best = entry
        if best is not None:
            entries.append(best)
    entries.sort(key=lambda item: (item.score, item.mark), reverse=True)
    return entries[: max(1, limit)]
