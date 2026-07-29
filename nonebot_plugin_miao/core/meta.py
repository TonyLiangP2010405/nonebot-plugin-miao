"""元数据加载器：加载 resources 下的角色/武器/圣遗物/卡池等静态元数据

懒加载 + 模块级缓存。ESM 格式的 .js 元数据（alias.js / extra.js / pool.js 等）
通过 quickjs 求值（见 _eval_esm）。图片资源不打包，路径助手只返回相对
resources 的路径字符串，不做存在性检查（渲染时走 CDN）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import quickjs

from .store import GAMES

# resources 目录：nonebot_plugin_miao/resources
RES_DIR = Path(__file__).resolve().parent.parent / "resources"

# ---------------------------------------------------------------------------
# ESM 求值
# ---------------------------------------------------------------------------

# resources 里的部分 .js 元数据文件带 import（lodash / Format），
# 但它们只用到了 forEach 与 pct/comma/percent，这里给出等价 stub。
# Format 实现参考 refs/miao-plugin/components/Format.js
_JS_STUBS = """
var lodash = {
  forEach: function (obj, fn) {
    Object.keys(obj).forEach(function (k) { fn(obj[k], k) })
  }
};
var Format = {
  pct: function (num, fix) { if (fix === undefined) fix = 1; return (num * 1).toFixed(fix) + '%' },
  comma: function (num, fix) {
    if (fix === undefined) fix = 0;
    return String(parseFloat((num * 1).toFixed(fix)))
  },
  percent: function (num, fix) { return Format.pct(num * 100, fix) }
};
"""

# 注意这些元数据文件普遍不写分号，字符类必须排除换行，否则会跨行吞掉后续代码
_RE_IMPORT = re.compile(r"^[ \t]*import[ \t][^\n;]*;?[ \t]*$", re.M)
_RE_EXPORT_STAR = re.compile(r"^[ \t]*export[ \t]*\*[^\n;]*;?[ \t]*$", re.M)
_RE_EXPORT_BRACE = re.compile(r"^[ \t]*export[ \t]*\{[^}\n]*\}[ \t]*;?[ \t]*$", re.M)
_RE_EXPORT_KW = re.compile(r"\bexport\s+(?=(?:const|let|var|function|class)\b)")


def _strip_esm(code: str) -> str:
    """剥离 ESM 的 import/export 语法，转成可在 quickjs 中直接 eval 的脚本"""
    code = _RE_IMPORT.sub("", code)
    code = _RE_EXPORT_STAR.sub("", code)
    code = _RE_EXPORT_BRACE.sub("", code)
    code = code.replace("export default ", "const __default__ = ")
    code = _RE_EXPORT_KW.sub("", code)
    return code


def _eval_esm(path: Path | str, export_names: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """求值无（或有可 stub 掉的）import 的 ESM 文件，返回指定导出变量组成的 dict

    实现方式：读文件 → 正则剥离 import 行与 export 关键字 → 注入 lodash/Format stub
    → 在 quickjs.Context 中 eval，脚本末尾用 JSON.stringify 收集指定变量
    （不存在的变量经 typeof 守卫跳过）→ Python 侧 json.loads。
    """
    path = Path(path)
    code = path.read_text(encoding="utf-8")
    code = _strip_esm(code)
    # 末尾用 JSON.stringify 收集导出变量；typeof 守卫让缺失的导出被 JSON 序列化时丢弃
    collect = ", ".join(f"{json.dumps(n)}: (typeof {n} === 'undefined' ? undefined : {n})" for n in export_names)
    script = f"{_JS_STUBS}\n{code}\n;JSON.stringify({{{collect}}})"
    ctx = quickjs.Context()
    result = ctx.eval(script)
    return json.loads(result)


# ---------------------------------------------------------------------------
# 通用 JSON 加载（带缓存）
# ---------------------------------------------------------------------------

_JSON_CACHE: dict[Path, Any] = {}


def _load_res_json(path: Path) -> Any:
    """加载 resources 下的 JSON 文件并缓存"""
    path = Path(path)
    if path not in _JSON_CACHE:
        with path.open(encoding="utf-8") as f:
            _JSON_CACHE[path] = json.load(f)
    return _JSON_CACHE[path]


def _check_game(game: str) -> None:
    if game not in GAMES:
        raise ValueError(f"非法游戏标识: {game!r}，仅支持 {GAMES}")


def _meta_dir(game: str) -> Path:
    _check_game(game)
    return RES_DIR / f"meta-{game}"


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------


@dataclass
class CharacterMeta:
    """角色元数据，data 为 data.json 原样透传的 dict

    常用字段（gs）：id/name/abbr/star/elem/weapon/baseAttr/growAttr/talentId/talentCons/talent
    sr 另有 tree/sp。也可通过属性访问 data 里的任意字段（如 meta.talentData）。
    """

    game: str
    data: dict[str, Any]

    def __getattr__(self, item: str) -> Any:
        try:
            return self.data[item]
        except KeyError:
            raise AttributeError(item) from None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class _CharIndex:
    """角色索引：按 名字/别名/abbr/id 都能找到角色目录名"""

    by_name: dict[str, str]  # name -> 目录名
    by_alias: dict[str, str]  # 别名/abbr -> 目录名
    by_id: dict[str, str]  # id -> 目录名


_CHAR_INDEX: dict[str, _CharIndex] = {}


def _char_index(game: str) -> _CharIndex:
    """构建（并缓存）角色索引"""
    if game in _CHAR_INDEX:
        return _CHAR_INDEX[game]
    char_dir = _meta_dir(game) / "character"
    by_name: dict[str, str] = {}
    by_alias: dict[str, str] = {}
    by_id: dict[str, str] = {}

    # 目录名 -> data.json 中的规范名/id/abbr
    dir_to_name: dict[str, str] = {}
    for sub in sorted(char_dir.iterdir()):
        data_file = sub / "data.json"
        if not sub.is_dir() or not data_file.is_file():
            continue
        data = _load_res_json(data_file)
        name = data.get("name") or sub.name
        dir_to_name[sub.name] = name
        by_name[name] = sub.name
        by_name.setdefault(sub.name, sub.name)
        if data.get("id") is not None:
            by_id[str(data["id"])] = sub.name
        if data.get("abbr") and data["abbr"] != name:
            by_alias[str(data["abbr"])] = sub.name

    # alias.js：{角色名: '别名1,别名2,...'}；sr 另有 abbr：{角色名: 简称}
    alias_file = char_dir / "alias.js"
    if alias_file.is_file():
        exports = _eval_esm(alias_file, ["alias", "abbr"])
        for char_name, aliases in (exports.get("alias") or {}).items():
            dir_name = by_name.get(char_name) or dir_to_name.get(char_name)
            if not dir_name:
                continue
            for one in str(aliases).split(","):
                one = one.strip()
                if one:
                    by_alias.setdefault(one, dir_name)
        for char_name, ab in (exports.get("abbr") or {}).items():
            dir_name = by_name.get(char_name) or dir_to_name.get(char_name)
            if dir_name and ab:
                by_alias.setdefault(str(ab), dir_name)

    index = _CharIndex(by_name=by_name, by_alias=by_alias, by_id=by_id)
    _CHAR_INDEX[game] = index
    return index


def get_character(name_or_alias: str | int, game: str) -> CharacterMeta | None:
    """按 名字/别名/abbr/id 查找角色元数据，找不到返回 None"""
    index = _char_index(game)
    key = str(name_or_alias).strip()
    dir_name = index.by_name.get(key) or index.by_alias.get(key) or index.by_id.get(key)
    if not dir_name:
        return None
    data = _load_res_json(_meta_dir(game) / "character" / dir_name / "data.json")
    return CharacterMeta(game=game, data=data)


# ---------------------------------------------------------------------------
# 武器
# ---------------------------------------------------------------------------

_WEAPON_INDEX: dict[str, dict[str, tuple[str, str]]] = {}  # game -> {名字/别名: (类型目录, 武器目录)}


def _weapon_index(game: str) -> dict[str, tuple[str, str]]:
    """构建（并缓存）武器索引：名字/abbr -> (类型目录, 武器目录)"""
    if game in _WEAPON_INDEX:
        return _WEAPON_INDEX[game]
    weapon_dir = _meta_dir(game) / "weapon"
    index: dict[str, tuple[str, str]] = {}
    for type_dir in sorted(weapon_dir.iterdir()):
        if not type_dir.is_dir():
            continue
        for sub in sorted(type_dir.iterdir()):
            data_file = sub / "data.json"
            if not sub.is_dir() or not data_file.is_file():
                continue
            data = _load_res_json(data_file)
            name = data.get("name") or sub.name
            index[name] = (type_dir.name, sub.name)
            index.setdefault(sub.name, (type_dir.name, sub.name))
    # alias.js：gs 为 {武器名: 简称}
    alias_file = weapon_dir / "alias.js"
    if alias_file.is_file():
        exports = _eval_esm(alias_file, ["alias", "abbr"])
        for aliases in (exports.get("alias") or {}, exports.get("abbr") or {}):
            for name, ab in aliases.items():
                if name in index:
                    for one in str(ab).split(","):
                        one = one.strip()
                        if one:
                            index.setdefault(one, index[name])
    _WEAPON_INDEX[game] = index
    return index


def get_weapon(name_or_alias: str, game: str) -> dict[str, Any] | None:
    """按 名字/简称 查找武器元数据（data.json 原样透传 + type 字段），找不到返回 None"""
    index = _weapon_index(game)
    key = str(name_or_alias).strip()
    hit = index.get(key)
    if not hit:
        return None
    type_dir, dir_name = hit
    data = dict(_load_res_json(_meta_dir(game) / "weapon" / type_dir / dir_name / "data.json"))
    data.setdefault("type", type_dir)
    return data


_WEAPON_ID_INDEX: dict[str, dict[str, tuple[str, str]]] = {}  # game -> {id: (类型目录, 武器目录)}


def get_weapon_by_id(weapon_id: str | int, game: str) -> dict[str, Any] | None:
    """按武器 id 查找武器元数据，找不到返回 None

    enka 的 weapon.itemId 与 mihomo 的 equipment.tid 均与 miao meta 的武器 id 一致
    （对齐 refs/miao-plugin EnkaData.js getWeapon 的 Weapon.get(ds.itemId)）。
    """
    _check_game(game)
    if game not in _WEAPON_ID_INDEX:
        index: dict[str, tuple[str, str]] = {}
        weapon_dir = _meta_dir(game) / "weapon"
        for type_dir in sorted(weapon_dir.iterdir()):
            if not type_dir.is_dir():
                continue
            for sub in sorted(type_dir.iterdir()):
                data_file = sub / "data.json"
                if not sub.is_dir() or not data_file.is_file():
                    continue
                data = _load_res_json(data_file)
                if data.get("id") is not None:
                    index[str(data["id"])] = (type_dir.name, sub.name)
        _WEAPON_ID_INDEX[game] = index
    hit = _WEAPON_ID_INDEX[game].get(str(weapon_id).strip())
    if not hit:
        return None
    type_dir, dir_name = hit
    data = dict(_load_res_json(_meta_dir(game) / "weapon" / type_dir / dir_name / "data.json"))
    data.setdefault("type", type_dir)
    return data


# ---------------------------------------------------------------------------
# 圣遗物 / 遗器
# ---------------------------------------------------------------------------


def get_artifact_meta(game: str) -> dict[str, Any]:
    """套装/散件定义：resources/meta-{game}/artifact/data.json"""
    return _load_res_json(_meta_dir(game) / "artifact" / "data.json")


_EXTRA_EXPORTS = ["mainAttr", "subAttr", "attrMap", "basicNum", "attrPct", "attrNameMap", "mainIdMap", "attrIdMap"]
_EXTRA_CACHE: dict[str, dict[str, Any]] = {}


def artifact_star_meta(game: str) -> dict[str, Any]:
    """遗器星级数值表 meta.json（sr 的 mainIdx/starData）；gs 无此文件返回 {}"""
    path = _meta_dir(game) / "artifact" / "meta.json"
    return _load_res_json(path) if path.is_file() else {}


def artifact_extra(game: str) -> dict[str, Any]:
    """词条常量表（attrMap/mainAttr/subAttr/attrPct 等）

    原神在 artifact/extra.js，星铁在 artifact/meta.js，均为 ESM，用 quickjs 求值。
    """
    if game in _EXTRA_CACHE:
        return _EXTRA_CACHE[game]
    arti_dir = _meta_dir(game) / "artifact"
    extra_file = arti_dir / "extra.js" if (arti_dir / "extra.js").is_file() else arti_dir / "meta.js"
    result = _eval_esm(extra_file, _EXTRA_EXPORTS)
    _EXTRA_CACHE[game] = result
    return result


# ---------------------------------------------------------------------------
# 卡池
# ---------------------------------------------------------------------------

_POOL_CACHE: dict[str, dict[str, Any]] = {}


def pool_info(game: str) -> dict[str, Any]:
    """卡池原始导出：gs 含 poolName/poolDetail/mixPoolDetail，sr 含 poolNameSr/poolDetailSr"""
    if game in _POOL_CACHE:
        return _POOL_CACHE[game]
    if game == "gs":
        result = _eval_esm(_meta_dir(game) / "info" / "pool.js", ["poolName", "poolDetail", "mixPoolDetail"])
    elif game == "sr":
        result = _eval_esm(_meta_dir(game) / "info" / "index.js", ["poolNameSr", "poolDetailSr"])
    else:
        raise ValueError(f"非法游戏标识: {game!r}，仅支持 {GAMES}")
    _POOL_CACHE[game] = result
    return result


def pool_data(game: str) -> list[dict[str, Any]]:
    """卡池版本表（poolDetail / poolDetailSr），元素含 version/half/from/to/char5/char4/weapon5/weapon4"""
    info = pool_info(game)
    return info.get("poolDetail") or info.get("poolDetailSr") or []


# ---------------------------------------------------------------------------
# 模拟抽卡配置
# ---------------------------------------------------------------------------


def gacha_sim_config() -> dict[str, Any]:
    """模拟抽卡配置：resources/gacha-sim/{gacha,pool,set}.json"""
    sim_dir = RES_DIR / "gacha-sim"
    return {
        "gacha": _load_res_json(sim_dir / "gacha.json"),
        "pool": _load_res_json(sim_dir / "pool.json"),
        "set": _load_res_json(sim_dir / "set.json"),
    }


# ---------------------------------------------------------------------------
# 图片路径助手（不检查文件是否存在，渲染时走 CDN）
# 命名规则参考 refs/miao-plugin/models/character/CharImg.js 与 models/Weapon.js
# ---------------------------------------------------------------------------

# gs 角色图片 kind -> 文件名（相对角色目录）
_GS_CHAR_IMGS = {
    "face": "imgs/face.webp",
    "face-q": "imgs/face-q.webp",
    "side": "imgs/side.webp",
    "gacha": "imgs/gacha.webp",
    "splash": "imgs/splash.webp",
    "card": "imgs/card.webp",
    "banner": "imgs/banner.webp",
}

# sr 角色图片 kind -> 文件名（相对角色目录）
_SR_CHAR_IMGS = {
    "face": "imgs/face.webp",
    "face-q": "imgs/face-q.webp",
    "splash": "imgs/splash.webp",
    "preview": "imgs/preview.webp",
}

# sr 技能图标（CharImg.getImgsSr）
_SR_TALENT_KEYS = ("a", "e", "q", "t", "z", "a2", "e2", "q2", "xe", "me", "mt")

# kind 别名归一化
_KIND_ALIAS = {
    "qface": "face-q",
    "faceq": "face-q",
    "q-face": "face-q",
}


def _norm_kind(kind: str) -> str:
    kind = str(kind).strip().lower().replace("_", "-")
    return _KIND_ALIAS.get(kind, kind)


def char_img(name: str, kind: str, game: str) -> str:
    """角色图片相对 resources 的路径（不检查存在性）

    gs kind：face/face-q/side/gacha/splash/card/banner/cons{1-6}/passive{0-4}/talent-{a,e,q}
    sr kind：face/face-q/splash/preview/tree-{1-4}/cons{1,2,4,6}/talent-{a,e,q,t,z,...}/banner/card
    """
    _check_game(game)
    kind = _norm_kind(kind)
    # 尽量解析为规范角色名（目录名）
    char = get_character(name, game)
    char_name = char.name if char else str(name).strip()

    base = f"meta-{game}/character/{char_name}"
    if game == "gs":
        if kind in _GS_CHAR_IMGS:
            return f"{base}/{_GS_CHAR_IMGS[kind]}"
        m = re.fullmatch(r"cons-?([1-6])", kind)
        if m:
            return f"{base}/icons/cons-{m.group(1)}.webp"
        m = re.fullmatch(r"passive-?([0-4])", kind)
        if m:
            return f"{base}/icons/passive-{m.group(1)}.webp"
        if kind in ("talent-a", "talent-e", "talent-q"):
            return f"{base}/icons/{kind}.webp"
    else:
        if kind in _SR_CHAR_IMGS:
            return f"{base}/{_SR_CHAR_IMGS[kind]}"
        # banner/card 是 sr 的公共资源
        if kind in ("banner", "card"):
            return f"meta-sr/character/common/imgs/{kind}.webp"
        m = re.fullmatch(r"tree-?([1-4])", kind)
        if m:
            return f"{base}/imgs/tree-{m.group(1)}.webp"
        m = re.fullmatch(r"cons-?([1-6])", kind)
        if m and m.group(1) not in ("3", "5"):
            return f"{base}/imgs/cons-{m.group(1)}.webp"
        m = re.fullmatch(r"talent-([a-z]{1,2}[0-9]?)", kind)
        if m and m.group(1) in _SR_TALENT_KEYS:
            return f"{base}/imgs/{kind}.webp"
    raise ValueError(f"未知的角色图片 kind: {kind!r}（game={game}）")


def weapon_img(name: str, kind: str, game: str) -> str:
    """武器图片相对 resources 的路径（不检查存在性）

    gs kind：icon/awaken（icon2）/gacha；sr kind：icon/icon2/gacha（splash）
    """
    _check_game(game)
    kind = _norm_kind(kind)
    index = _weapon_index(game)
    hit = index.get(str(name).strip())
    if not hit:
        raise ValueError(f"未知的武器: {name!r}（game={game}）")
    type_dir, dir_name = hit
    base = f"meta-{game}/weapon/{type_dir}/{dir_name}"
    if game == "gs":
        files = {"icon": "icon.webp", "awaken": "awaken.webp", "icon2": "awaken.webp", "gacha": "gacha.webp"}
    else:
        files = {"icon": "icon.webp", "icon2": "icon-s.webp", "gacha": "splash.webp", "splash": "splash.webp"}
    if kind not in files:
        raise ValueError(f"未知的武器图片 kind: {kind!r}（game={game}）")
    return f"{base}/{files[kind]}"
