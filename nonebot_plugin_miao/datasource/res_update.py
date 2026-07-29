"""游戏元数据运行时更新（对标 miao-plugin 的 #喵喵更新）

打包在 wheel 里的文本元数据（resources/meta-gs、meta-sr、gacha-sim）会随游戏
版本过期。本模块运行时从上游仓库经 jsDelivr CDN 拉取最新元数据到本地覆盖目录
（localstore 数据目录下的 meta_override/），core/meta.py 加载时覆盖目录优先、
打包资源兜底。

链路：data.jsdelivr.com 拿文件树（嵌套 JSON）→ 筛选文本文件 →
cdn.jsdelivr.net 并发下载。jsDelivr 对超过 50MB 的仓库拒绝返回文件树（403），
此时回退 GitHub trees API（api.github.com 通常可直连，无需 token）。
文件筛选规则与 tools/sync_resources.py 完全一致：
meta-gs/meta-sr 下的 .json/.js（排除 imgs/icons/splash 目录与图片扩展名），
外加 Yunzai-genshin 的 defSet/gacha/{gacha,pool,set}.yaml（转 JSON）。

原子性：先完整下载到临时目录，关键文件校验通过后才整体替换覆盖目录，
任何失败都不会留下半截状态。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import httpx
import yaml

from ..core import store

MIAO_REPO = "yoimiya-kokomi/miao-plugin"
YUNZAI_REPO = "TimeRainStarSky/Yunzai-genshin"

_DATA_API = "https://data.jsdelivr.com/v1/packages/gh"
_CDN_BASE = "https://cdn.jsdelivr.net/gh"
_GITHUB_API = "https://api.github.com/repos"

# 与 tools/sync_resources.py 一致的筛选规则
IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}
EXCLUDED_DIRS = {"imgs", "icons", "splash"}
META_EXTS = {".json", ".js"}
GACHA_YAMLS = ("gacha.yaml", "pool.yaml", "set.yaml")

CONCURRENCY = 16
RETRIES = 2  # 单文件失败后的重试次数，之后跳过并记录
TIMEOUT = 30
# 关键文件校验：两游角色 data.json 总量必须超过该值（防止拉到残缺文件树）
MIN_TOTAL_CHARS = 100

_UPDATE_INFO_FILE = "meta_update.json"


def override_dir() -> Path:
    """元数据覆盖目录：localstore 数据目录下的 meta_override/

    nonebot_plugin_localstore 在 import 时就要求 nonebot 已初始化，故延迟到调用时导入
    （参照 core/store.py 的写法）。
    """
    from nonebot_plugin_localstore import get_plugin_data_dir

    return Path(get_plugin_data_dir()) / "meta_override"


def using_override() -> bool:
    """当前是否使用覆盖目录的元数据（覆盖目录存在且含 meta-gs）"""
    try:
        ov = override_dir()
    except Exception:
        return False
    return ov.is_dir() and (ov / "meta-gs").is_dir()


def last_update_info() -> dict[str, Any] | None:
    """上次更新信息 {time, ok, failed_count}，从未更新过返回 None"""
    return store.load_json(override_dir().parent / _UPDATE_INFO_FILE)


# ---------------------------------------------------------------------------
# 文件树与筛选
# ---------------------------------------------------------------------------


async def _list_via_jsdelivr(repo: str) -> list[str]:
    """jsDelivr data API：嵌套 files 树扁平化

    响应：{"files": [{"type": "directory", "name": ..., "files": [...]}, {"type": "file", ...}]}
    注意 jsDelivr 对超过 50MB 的包返回 403（Package size exceeded），由调用方回退。
    """
    url = f"{_DATA_API}/{repo}@master"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    files: list[str] = []

    def walk(nodes: list[dict[str, Any]] | None, prefix: str = "") -> None:
        for node in nodes or []:
            path = f"{prefix}/{node['name']}" if prefix else node["name"]
            if node.get("type") == "directory":
                walk(node.get("files"), path)
            else:
                files.append(path)

    walk(data.get("files"))
    return files


async def _list_via_github(repo: str) -> list[str]:
    """GitHub trees API（递归）：扁平 tree 列表，取 blob 项的 path"""
    url = f"{_GITHUB_API}/{repo}/git/trees/master?recursive=1"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if data.get("truncated"):
        raise ValueError(f"GitHub trees API 返回被截断的文件树: {repo}")
    return [e["path"] for e in data.get("tree") or [] if e.get("type") == "blob"]


async def list_remote_files(repo: str) -> list[str]:
    """拿仓库全部文件相对路径列表：jsDelivr data API 优先，失败回退 GitHub trees API

    repo 形如 "yoimiya-kokomi/miao-plugin"。
    """
    try:
        return await _list_via_jsdelivr(repo)
    except (httpx.HTTPError, ValueError, KeyError):
        return await _list_via_github(repo)


def select_meta_files(files: list[str]) -> list[str]:
    """从仓库文件列表筛选需同步的元数据（与 tools/sync_resources.py 规则一致）

    resources/meta-gs、meta-sr 下的 .json/.js，排除 imgs/icons/splash 目录与图片扩展名。
    返回带 resources/ 前缀的仓库相对路径。
    """
    result = []
    for path in files:
        p = PurePosixPath(path)
        parts = p.parts
        if len(parts) < 3 or parts[0] != "resources" or parts[1] not in ("meta-gs", "meta-sr"):
            continue
        if EXCLUDED_DIRS & set(parts[2:-1]):
            continue
        if p.suffix.lower() in IMAGE_EXTS:
            continue
        if p.suffix.lower() not in META_EXTS:
            continue
        result.append(path)
    return sorted(result)


# ---------------------------------------------------------------------------
# 并发下载
# ---------------------------------------------------------------------------


async def download_files(
    repo: str,
    paths: list[str],
    dest_dir: Path | str,
    on_progress: Callable[[int, int, str], Any] | None = None,
) -> dict[str, Any]:
    """从 jsDelivr CDN 并发下载文件到 dest_dir（保持相对路径）

    单文件失败重试 RETRIES 次后跳过并记录。返回 {ok: 成功数, failed: [失败路径]}。
    on_progress(done, total, path) 在每个文件落盘后调用。
    """
    dest = Path(dest_dir)
    total = len(paths)
    failed: list[str] = []
    done = 0
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:

        async def fetch(rel: str) -> None:
            nonlocal done
            url = f"{_CDN_BASE}/{repo}@master/{rel}"
            resp: httpx.Response | None = None
            async with sem:
                for attempt in range(RETRIES + 1):
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            resp = r
                            break
                    except httpx.HTTPError:
                        pass
                    if attempt < RETRIES:
                        await asyncio.sleep(0.5 * (attempt + 1))
                if resp is None:
                    failed.append(rel)
                    return
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(resp.content)
                done += 1
                if on_progress:
                    on_progress(done, total, rel)

        await asyncio.gather(*(fetch(p) for p in paths))
    return {"ok": done, "failed": failed}


# ---------------------------------------------------------------------------
# 编排：列文件 → 筛选 → 下载 → 校验 → 原子替换
# ---------------------------------------------------------------------------


def _count_chars(staged: Path, game_dir: str) -> int:
    """统计已下载的角色 data.json 数量"""
    char_dir = staged / game_dir / "character"
    if not char_dir.is_dir():
        return 0
    return sum(1 for sub in char_dir.iterdir() if sub.is_dir() and (sub / "data.json").is_file())


def _swap(staged: Path, dest: Path) -> None:
    """原子替换覆盖目录：旧目录先改名备份，新目录就位后再删备份，失败回滚"""
    backup = dest.parent / f".{dest.name}.bak"
    shutil.rmtree(backup, ignore_errors=True)
    if dest.exists():
        os.replace(dest, backup)
    try:
        os.replace(staged, dest)
    except BaseException:
        if backup.exists() and not dest.exists():
            os.replace(backup, dest)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _error(start: float, reason: str, failed: list[str] | None = None) -> dict[str, Any]:
    return {
        "code": "error",
        "reason": reason,
        "failed": failed or [],
        "duration": time.monotonic() - start,
    }


async def update_resources(on_progress: Callable[[int, int, str], Any] | None = None) -> dict[str, Any]:
    """拉取上游最新元数据并原子替换覆盖目录

    成功返回 {code: "ok", gs_chars, sr_chars, pools, failed, duration}；
    校验不通过时不替换旧覆盖目录，返回 {code: "error", reason, failed, duration}。
    成功后清空 core.meta 的全部缓存并记录 meta_update.json。
    """
    start = time.monotonic()
    dest = override_dir()
    work = dest.parent / f".{dest.name}.work"
    staged = work / "resources"
    shutil.rmtree(work, ignore_errors=True)
    staged.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    ok_count = 0
    try:
        try:
            files = await list_remote_files(MIAO_REPO)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            return _error(start, f"获取 miao-plugin 文件列表失败：{e}")
        ret = await download_files(MIAO_REPO, select_meta_files(files), work, on_progress)
        ok_count += ret["ok"]
        failed.extend(ret["failed"])

        yz_paths = [f"defSet/gacha/{name}" for name in GACHA_YAMLS]
        ret = await download_files(YUNZAI_REPO, yz_paths, work, on_progress)
        ok_count += ret["ok"]
        failed.extend(ret["failed"])

        # Yunzai 卡池 yaml → gacha-sim/*.json（gacha.yaml 带 BOM，统一 utf-8-sig）
        try:
            sim_dir = staged / "gacha-sim"
            sim_dir.mkdir(parents=True, exist_ok=True)
            for name in GACHA_YAMLS:
                src = work / "defSet" / "gacha" / name
                data = yaml.safe_load(src.read_text(encoding="utf-8-sig"))
                (sim_dir / (Path(name).stem + ".json")).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
        except (OSError, yaml.YAMLError) as e:
            return _error(start, f"卡池配置转换失败：{e}", failed)

        # 关键文件校验，全部通过才替换覆盖目录
        gs_chars = _count_chars(staged, "meta-gs")
        sr_chars = _count_chars(staged, "meta-sr")
        if gs_chars + sr_chars <= MIN_TOTAL_CHARS:
            return _error(start, f"角色元数据不完整（gs {gs_chars} + sr {sr_chars} 个，疑似拉取失败）", failed)
        if not (staged / "meta-gs" / "info" / "pool.js").is_file():
            return _error(start, "缺少关键文件 meta-gs/info/pool.js", failed)
        for stem in ("gacha", "pool", "set"):
            if not (staged / "gacha-sim" / f"{stem}.json").is_file():
                return _error(start, f"缺少卡池配置 gacha-sim/{stem}.json", failed)

        _swap(staged, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    pools_raw = store.load_json(dest / "gacha-sim" / "pool.json", [])
    pools = len(pools_raw) if isinstance(pools_raw, list) else 0

    # 覆盖目录已切换，清空 meta 缓存让后续加载走新目录
    from ..core import meta

    meta.clear_cache()

    store.save_json(
        dest.parent / _UPDATE_INFO_FILE,
        {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": ok_count, "failed_count": len(failed)},
    )
    return {
        "code": "ok",
        "gs_chars": gs_chars,
        "sr_chars": sr_chars,
        "pools": pools,
        "failed": failed,
        "duration": time.monotonic() - start,
    }
