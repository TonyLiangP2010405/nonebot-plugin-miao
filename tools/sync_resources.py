#!/usr/bin/env python3
"""开发期资源同步脚本。

从 refs/ 参考仓库同步静态资源到 nonebot_plugin_miao/resources/：
- refs/miao-plugin/resources/meta-gs、meta-sr 的文本元数据（*.json / *.js），
  排除图片（.webp/.png/.jpg/.gif）及 imgs/、icons/、splash/ 目录；
- refs/miao-plugin/resources/common/font/ 的字体文件 → resources/fonts/
  （woff 会同时用 fontTools 转出同名 .ttf：skia 的 Windows 构建不认 woff，
  渲染时 TTF 优先加载；fontTools 缺失时只告警不中断）；
- refs/miao-plugin/resources/gacha/imgs/ 的占位图 → resources/imgs/；
- refs/Yunzai-genshin/defSet/gacha/*.yaml → resources/gacha-sim/*.json
  （pool.json 随后会被 meta-gs/info/pool.js 派生的最新数据覆盖，见
  sync_pool_from_pool_js）。

用法（项目根目录）：
    poetry run python tools/sync_resources.py
"""
import json
import shutil
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFS_ROOT = PROJECT_ROOT.parent / "refs"

MIAO_RES = REFS_ROOT / "miao-plugin" / "resources"
YUNZAI_GACHA = REFS_ROOT / "Yunzai-genshin" / "defSet" / "gacha"

TARGET = PROJECT_ROOT / "nonebot_plugin_miao" / "resources"

IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}
EXCLUDED_DIRS = {"imgs", "icons", "splash"}

FONTS = ["HYWH-65W.woff", "NZBZ.woff", "tttgbnumber.woff"]
PLACEHOLDER_IMGS = ["no-avatar.webp", "date-icon.webp"]
GACHA_YAMLS = ["gacha.yaml", "pool.yaml", "set.yaml"]


def sync_meta() -> int:
    """同步 meta-gs / meta-sr 文本元数据，返回复制文件数。"""
    count = 0
    for game in ("meta-gs", "meta-sr"):
        src_dir = MIAO_RES / game
        if not src_dir.is_dir():
            print(f"[WARN] 源目录不存在，跳过: {src_dir}", file=sys.stderr)
            continue
        for src in sorted(src_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(src_dir)
            if EXCLUDED_DIRS & set(rel.parts[:-1]):
                continue
            if src.suffix.lower() in IMAGE_EXTS:
                continue
            if src.suffix.lower() not in {".json", ".js"}:
                continue
            dst = TARGET / game / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    return count


def sync_fonts() -> int:
    """同步字体文件到 resources/fonts/，并把 woff 转出同名 .ttf（渲染端 TTF 优先）。"""
    count = 0
    src_dir = MIAO_RES / "common" / "font"
    for name in FONTS:
        src = src_dir / name
        if not src.is_file():
            print(f"[WARN] 字体缺失，跳过: {src}", file=sys.stderr)
            continue
        dst = TARGET / "fonts" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
        try:
            from fontTools.ttLib import TTFont

            ttf = TTFont(dst)
            ttf.flavor = None
            ttf.save(dst.with_suffix(".ttf"))
        except ImportError:
            print("[WARN] 未安装 fontTools，跳过 woff→ttf 转换", file=sys.stderr)
    return count


def sync_placeholder_imgs() -> int:
    """同步渲染占位图到 resources/imgs/。"""
    count = 0
    src_dir = MIAO_RES / "gacha" / "imgs"
    for name in PLACEHOLDER_IMGS:
        src = src_dir / name
        if not src.is_file():
            print(f"[WARN] 占位图缺失，跳过: {src}", file=sys.stderr)
            continue
        dst = TARGET / "imgs" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def sync_gacha_yamls() -> int:
    """Yunzai-genshin 卡池 yaml 转 JSON 到 resources/gacha-sim/。"""
    count = 0
    for name in GACHA_YAMLS:
        src = YUNZAI_GACHA / name
        if not src.is_file():
            print(f"[WARN] 卡池配置缺失，跳过: {src}", file=sys.stderr)
            continue
        # gacha.yaml 带 UTF-8 BOM，统一用 utf-8-sig 读取
        data = yaml.safe_load(src.read_text(encoding="utf-8-sig"))
        dst = TARGET / "gacha-sim" / (src.stem + ".json")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        count += 1
    return count


def sync_pool_from_pool_js() -> bool:
    """卡池列表优先从 miao-plugin 的 meta-gs/info/pool.js 派生（yaml 上游已停更）

    pool.js 解析成功且其最新池不旧于 yaml 数据时覆盖 resources/gacha-sim/pool.json。
    返回是否发生了覆盖。
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from nonebot_plugin_miao.datasource.res_update import (
        _newest_end_time,
        pools_from_pool_js,
    )

    pool_js = MIAO_RES / "meta-gs" / "info" / "pool.js"
    if not pool_js.is_file():
        print(f"[WARN] pool.js 缺失，跳过卡池派生: {pool_js}", file=sys.stderr)
        return False
    try:
        js_pools = pools_from_pool_js(pool_js)
    except Exception as e:
        print(f"[WARN] pool.js 卡池派生失败，沿用 yaml 数据: {e}", file=sys.stderr)
        return False
    yaml_path = TARGET / "gacha-sim" / "pool.json"
    yaml_pools = json.loads(yaml_path.read_text(encoding="utf-8")) if yaml_path.is_file() else []
    if not js_pools or _newest_end_time(js_pools) < _newest_end_time(yaml_pools):
        return False
    yaml_path.write_text(
        json.dumps(js_pools, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return True


def main() -> None:
    if not MIAO_RES.is_dir():
        sys.exit(f"[ERROR] miao-plugin 资源目录不存在: {MIAO_RES}")
    if not YUNZAI_GACHA.is_dir():
        sys.exit(f"[ERROR] Yunzai-genshin 卡池目录不存在: {YUNZAI_GACHA}")

    meta_count = sync_meta()
    font_count = sync_fonts()
    img_count = sync_placeholder_imgs()
    gacha_count = sync_gacha_yamls()
    pool_from_js = sync_pool_from_pool_js()

    print("资源同步完成：")
    print(f"  meta 文本元数据: {meta_count} 个文件 (meta-gs + meta-sr)")
    print(f"  字体文件:        {font_count} 个 → resources/fonts/")
    print(f"  占位图:          {img_count} 个 → resources/imgs/")
    print(f"  卡池配置:        {gacha_count} 个 → resources/gacha-sim/ (yaml→json)")
    print(f"  卡池列表来源:    {'pool.js 派生（最新）' if pool_from_js else 'yaml（pool.js 未更新，沿用）'}")
    print(f"  输出目录:        {TARGET}")


if __name__ == "__main__":
    main()
