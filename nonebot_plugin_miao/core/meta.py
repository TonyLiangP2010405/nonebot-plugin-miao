"""元数据加载器：加载 resources 下的角色/武器/圣遗物/卡池等静态元数据

懒加载 + 模块级缓存。ESM 格式的 .js 元数据（alias.js / extra.js / pool.js 等）
通过 quickjs 求值（见 _eval_esm）。图片资源不打包，路径助手只返回相对
resources 的路径字符串，不做存在性检查（渲染时走 CDN）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover - 依赖缺失时退回纯字形匹配
    lazy_pinyin = None

from .jseval import eval_js
from .store import GAMES

# resources 目录：nonebot_plugin_miao/resources（打包兜底）
RES_DIR = Path(__file__).resolve().parent.parent / "resources"


def _res_root() -> Path:
    """元数据根目录：运行时覆盖目录（/更新面板资源拉取）优先，打包 resources 兜底

    覆盖目录存在且含 meta-gs 时视为有效。localstore 依赖 nonebot 已初始化，
    未初始化（如直接 import 的单元测试）时回退打包资源。
    """
    try:
        from ..datasource.res_update import override_dir

        ov = override_dir()
    except Exception:
        return RES_DIR
    if ov.is_dir() and (ov / "meta-gs").is_dir():
        return ov
    return RES_DIR

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
    → 在 JS 引擎（core/jseval，quickjs 或 pythonmonkey）中 eval，脚本末尾用 JSON.stringify 收集指定变量
    （不存在的变量经 typeof 守卫跳过）→ Python 侧 json.loads。
    """
    path = Path(path)
    code = path.read_text(encoding="utf-8")
    code = _strip_esm(code)
    # 末尾用 JSON.stringify 收集导出变量；typeof 守卫让缺失的导出被 JSON 序列化时丢弃
    collect = ", ".join(f"{json.dumps(n)}: (typeof {n} === 'undefined' ? undefined : {n})" for n in export_names)
    script = f"{_JS_STUBS}\n{code}\n;JSON.stringify({{{collect}}})"
    result = eval_js(script)
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
    return _res_root() / f"meta-{game}"


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


# 星铁「加强角色」：mihomo 的 enhancedId=1 会把 1310 流萤解析成 2310 流萤Pro。
# 查询基础角色时允许回落到 Pro 数据，查询 Pro 时也允许回落到基础数据。
SR_ENHANCED_BASE_IDS = frozenset({1212, 1205, 1005, 1006, 1004, 1102, 1217, 1310, 1306, 1307})


def character_id_candidates(char_id: str | int, game: str) -> list[str]:
    """角色 id 查询顺序：自身 → 星铁加强/基础变体。"""
    try:
        cid = int(char_id)
    except (TypeError, ValueError):
        return [str(char_id)]
    ids = [str(cid)]
    if game == "sr":
        if cid in SR_ENHANCED_BASE_IDS:
            ids.append(str(cid + 1000))
        elif cid - 1000 in SR_ENHANCED_BASE_IDS:
            ids.append(str(cid - 1000))
    return ids


_CHAR_NORM_CACHE: dict[str, list[tuple[str, str, str]]] = {}
_PINYIN_CACHE: dict[str, tuple[str, ...]] = {}


def _pinyin_syllables(text: str) -> tuple[str, ...]:
    """按字/词取无声调拼音；依赖缺失时退回原字符，纯字形匹配仍可用。"""
    if text in _PINYIN_CACHE:
        return _PINYIN_CACHE[text]
    if lazy_pinyin is None:
        value = tuple(text)
    else:
        try:
            value = tuple(str(item).lower() for item in lazy_pinyin(text))
        except Exception:
            value = tuple(text)
    _PINYIN_CACHE[text] = value
    return value


def _is_pinyin_prefix(a: Sequence[str], b: Sequence[str]) -> bool:
    if len(a) > len(b):
        a, b = b, a
    return bool(a) and tuple(b[: len(a)]) == tuple(a)

# 指令关键词不做错字猜测，否则「抽卡」会被纠成「米卡」
_RESOLVE_STOPWORDS = {
    "抽卡", "抽奖", "更新", "面板", "列表", "伤害", "圣遗物", "遗器", "图鉴",
    "攻略", "角色", "武器", "卡池", "十连", "单抽", "记录", "统计", "分析",
    "排名", "排行", "绑定", "帮助", "确认", "取消", "参与", "模拟", "定轨",
    "导入", "导出", "签到", "面板次数", "面板开关",
}


def _norm_char_key(text: object) -> str:
    """角色名归一化：去空格/标点/分隔符，统一大小写，去掉“的/角色/酱”等尾巴。"""
    key = str(text or "").strip().lower()
    key = key.replace("　", " ")
    key = re.sub(r"\s+", "", key)
    key = re.sub(r"[·・•\-—–_/\\|.,，。!！?？:：;；'\"“”‘’()（）\[\]【】<>《》]", "", key)
    key = re.sub(r"(的|角色|人物|酱|亲)$", "", key)
    return key


def _char_candidates(game: str) -> list[tuple[str, str, str]]:
    """(归一化 key, 目录名, 展示名) 列表，用于别名与错字匹配。"""
    if game in _CHAR_NORM_CACHE:
        return _CHAR_NORM_CACHE[game]
    index = _char_index(game)
    names: dict[str, str] = {}
    for name, dir_name in index.by_name.items():
        names.setdefault(dir_name, name)
    candidates: dict[tuple[str, str], tuple[str, str, str]] = {}
    for key, dir_name in list(index.by_name.items()) + list(index.by_alias.items()):
        norm = _norm_char_key(key)
        if len(norm) < 2:
            continue
        candidates[(norm, dir_name)] = (norm, dir_name, names.get(dir_name, dir_name))
    result = list(candidates.values())
    _CHAR_NORM_CACHE[game] = result
    return result


def _levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def resolve_character(name_or_alias: str | int, game: str) -> CharacterMeta | None:
    """角色名解析：精确名字/别名/id → 归一化别名 → 常见错字容错。

    只在命令层使用；渲染/计算内部仍用 get_character 精确查找，避免海量头像
    重复做模糊匹配。数字 id 不做模糊匹配。
    """
    exact = get_character(name_or_alias, game)
    if exact is not None:
        return exact
    raw = str(name_or_alias or "").strip()
    if not raw or raw.isdigit():
        return None
    query = _norm_char_key(raw)
    if len(query) < 2 or query in _RESOLVE_STOPWORDS:
        return None

    candidates = _char_candidates(game)
    index = _char_index(game)
    name_norms = {_norm_char_key(key) for key in index.by_name}
    name_dirs = {
        dir_name for key, dir_name in index.by_name.items() if _norm_char_key(key) == query
    }
    if len(name_dirs) == 1:
        return get_character(next(iter(name_dirs)), game)
    if len(name_dirs) > 1:
        return None

    alias_dirs = {dir_name for norm, dir_name, _ in candidates if norm == query}
    if len(alias_dirs) == 1:
        return get_character(next(iter(alias_dirs)), game)
    if len(alias_dirs) > 1:
        return None

    py_query = _pinyin_syllables(query)
    scored: dict[str, tuple[int, int, int, float, int, int, str]] = {}
    for norm, dir_name, name in candidates:
        distance = _levenshtein(query, norm)
        ratio = SequenceMatcher(None, query, norm).ratio()
        py_candidate = _pinyin_syllables(norm)
        py_distance = _levenshtein(py_query, py_candidate)
        py_prefix = _is_pinyin_prefix(py_query, py_candidate)
        # 两字查询必须是同音字/同音前缀，避免把「可以」纠成「可莉」
        if len(query) == 2 and not py_prefix and py_query != py_candidate:
            continue
        # 两字短词只允许扩展规范角色名，不做「长别名前缀」，避免「那个」命中「那个女人」
        if len(query) == 2 and py_prefix and len(norm) > len(query) and norm not in name_norms:
            continue
        if distance > 1 and ratio < 0.74 and py_distance > 0 and not py_prefix:
            continue
        # 同等字形距离下优先同音、再优先规范角色名，避免被别人的两字别名抢走
        is_alias = 0 if norm in name_norms else 1
        score = (
            distance,
            py_distance,
            is_alias,
            -ratio,
            abs(len(query) - len(norm)),
            len(norm),
            name,
        )
        old = scored.get(dir_name)
        if old is None or score[:6] < old[:6]:
            scored[dir_name] = score
    if not scored:
        return None
    ranked = sorted(scored.items(), key=lambda item: item[1][:6])
    if len(ranked) > 1 and ranked[0][1][:3] == ranked[1][1][:3]:
        # 两个角色同样接近时不猜，交给调用方提示
        return None
    return get_character(ranked[0][0], game)


def suggest_characters(name_or_alias: str | int, game: str, limit: int = 3) -> list[str]:
    """错字/别名未命中时给出候选角色名（用于错误提示）。"""
    raw = str(name_or_alias or "").strip()
    if not raw:
        return []
    query = _norm_char_key(raw)
    if len(query) < 2 or query in _RESOLVE_STOPWORDS:
        return []
    index = _char_index(game)
    name_norms = {_norm_char_key(key) for key in index.by_name}
    py_query = _pinyin_syllables(query)
    scored: dict[str, tuple[float, int, int, int, str]] = {}
    for norm, dir_name, name in _char_candidates(game):
        ratio = SequenceMatcher(None, query, norm).ratio()
        distance = _levenshtein(query, norm)
        py_distance = _levenshtein(py_query, _pinyin_syllables(norm))
        if ratio < 0.5 and distance > 1 and py_distance > 1:
            continue
        score = (-ratio, py_distance, distance, 0 if norm in name_norms else 1, name)
        old = scored.get(dir_name)
        if old is None or score[:4] < old[:4]:
            scored[dir_name] = score
    ranked = sorted(scored.values(), key=lambda item: item[:4])
    result: list[str] = []
    for _, _, _, _, name in ranked:
        if name not in result:
            result.append(name)
        if len(result) >= max(1, limit):
            break
    return result


def list_characters(game: str) -> list[dict[str, Any]]:
    """返回全部角色的规范元数据，按星级降序、名称升序排列。"""
    index = _char_index(game)
    result: list[dict[str, Any]] = []
    for dir_name in set(index.by_name.values()):
        data_file = _meta_dir(game) / "character" / dir_name / "data.json"
        if data_file.is_file():
            result.append(dict(_load_res_json(data_file)))
    return sorted(result, key=lambda item: (-int(item.get("star") or 0), str(item.get("name") or "")))


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


def list_weapons(game: str) -> list[dict[str, Any]]:
    """返回全部武器的规范元数据，按类型、星级降序、名称升序排列。"""
    result: list[dict[str, Any]] = []
    for type_dir, dir_name in set(_weapon_index(game).values()):
        data = dict(_load_res_json(_meta_dir(game) / "weapon" / type_dir / dir_name / "data.json"))
        data.setdefault("type", type_dir)
        result.append(data)
    return sorted(
        result,
        key=lambda item: (str(item.get("type") or ""), -int(item.get("star") or 0), str(item.get("name") or "")),
    )


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
    """模拟抽卡配置：resources/gacha-sim/{gacha,pool,set}.json（覆盖目录优先）"""
    sim_dir = _res_root() / "gacha-sim"
    return {
        "gacha": _load_res_json(sim_dir / "gacha.json"),
        "pool": _load_res_json(sim_dir / "pool.json"),
        "set": _load_res_json(sim_dir / "set.json"),
    }


def clear_cache() -> None:
    """清空模块内全部缓存（覆盖目录更新后由 datasource/res_update 调用）

    清空后所有元数据按当时的 _res_root() 重新加载。
    """
    _JSON_CACHE.clear()
    _CHAR_INDEX.clear()
    _CHAR_NORM_CACHE.clear()
    _PINYIN_CACHE.clear()
    _WEAPON_INDEX.clear()
    _WEAPON_ID_INDEX.clear()
    _EXTRA_CACHE.clear()
    _POOL_CACHE.clear()


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


def artis_img(set_name: str, idx: int | str, game: str) -> str:
    """圣遗物 / 遗器部位图标相对 resources 的路径（不检查存在性）

    gs：meta-gs/artifact/imgs/{套装}/{部位 1-5}.webp
    sr：meta-sr/artifact/{套装}/arti-{部位 1-6}.webp
    """
    _check_game(game)
    set_name = str(set_name or "").strip()
    if not set_name:
        raise ValueError("圣遗物套装名为空")
    if game == "gs":
        return f"meta-gs/artifact/imgs/{set_name}/{int(idx)}.webp"
    return f"meta-sr/artifact/{set_name}/arti-{int(idx)}.webp"


def talent_img(name: str, key: str, game: str) -> str:
    """角色天赋图标相对 resources 的路径

    gs 普攻用武器类型图标（common/item/atk-{武器}），战技/爆发优先用提升该天赋的
    命座图标（talentCons），没有命座加成时才用 icons/talent-{key}.webp；
    sr 直接使用 imgs/talent-{key}.webp。
    """
    _check_game(game)
    kind = str(key or "").strip().lower()
    char = get_character(name, game)
    if not char:
        raise ValueError(f"未找到角色 meta: {name}")
    if game == "gs":
        if kind == "a":
            return f"common/item/atk-{char.get('weapon') or 'sword'}.webp"
        cons = (char.get("talentCons") or {}).get(kind) or 0
        if cons:
            return f"meta-gs/character/{char.name}/icons/cons-{cons}.webp"
        return f"meta-gs/character/{char.name}/icons/talent-{kind}.webp"
    return f"meta-sr/character/{char.name}/imgs/talent-{kind}.webp"


def cons_img(name: str, idx: int | str, game: str) -> str:
    """命座图标相对 resources 的路径（星铁 3/5 命使用被强化的天赋图标）"""
    _check_game(game)
    char = get_character(name, game)
    if not char:
        raise ValueError(f"未找到角色 meta: {name}")
    idx = int(idx)
    if game == "gs":
        return f"meta-gs/character/{char.name}/icons/cons-{idx}.webp"
    if idx in (3, 5):
        key = (char.get("talentCons") or {}).get(str(idx))
        if key:
            return f"meta-sr/character/{char.name}/imgs/talent-{key}.webp"
    return f"meta-sr/character/{char.name}/imgs/cons-{idx}.webp"
