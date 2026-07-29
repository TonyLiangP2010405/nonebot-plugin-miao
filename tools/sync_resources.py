#!/usr/bin/env python3
"""开发期资源同步脚本。

从 refs/ 参考仓库同步静态资源到 nonebot_plugin_miao/resources/：
- refs/miao-plugin/resources/meta-gs、meta-sr 的文本元数据（*.json / *.js），
  排除图片（.webp/.png/.jpg/.gif）及 imgs/、icons/、splash/ 目录；
- refs/miao-plugin/resources/common/font/ 的字体文件 → resources/fonts/；
- refs/miao-plugin/resources/gacha/imgs/ 的占位图 → resources/imgs/；
- refs/Yunzai-genshin/defSet/gacha/*.yaml → resources/gacha-sim/*.json。

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

FONTS = ["HYWH-65W.woff", "NZBZ.woff", "tttgbnumber.woff", "华文中宋.TTF"]
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
    """同步字体文件到 resources/fonts/。"""
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


def main() -> None:
    if not MIAO_RES.is_dir():
        sys.exit(f"[ERROR] miao-plugin 资源目录不存在: {MIAO_RES}")
    if not YUNZAI_GACHA.is_dir():
        sys.exit(f"[ERROR] Yunzai-genshin 卡池目录不存在: {YUNZAI_GACHA}")

    meta_count = sync_meta()
    font_count = sync_fonts()
    img_count = sync_placeholder_imgs()
    gacha_count = sync_gacha_yamls()

    print("资源同步完成：")
    print(f"  meta 文本元数据: {meta_count} 个文件 (meta-gs + meta-sr)")
    print(f"  字体文件:        {font_count} 个 → resources/fonts/")
    print(f"  占位图:          {img_count} 个 → resources/imgs/")
    print(f"  卡池配置:        {gacha_count} 个 → resources/gacha-sim/ (yaml→json)")
    print(f"  输出目录:        {TARGET}")


if __name__ == "__main__":
    main()
