"""用 fixture 跑 Python 版 analyse/stat，结果写 tools/py_out.json 供 verify_gacha.mjs 比对

数据目录直接指向 tools/fixtures（store._data_dir 打补丁），与 JS 版读同一批记录文件。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nonebot_plugin_miao.core import store  # noqa: E402

# 把数据目录指向 fixture 根（store 内部按 gacha/{game}/{user}/{uid}/{type}.json 拼路径）
store._data_dir = lambda: ROOT / "tools" / "fixtures"  # noqa: E402

from nonebot_plugin_miao.gacha import analyse as ga  # noqa: E402

# 与 verify_gacha.mjs 保持一致的用例表：(kind, user, uid, type, game)
CASES = [
    ("analyse", 1, "100000001", 301, "gs"),
    ("analyse", 1, "100000001", 302, "gs"),
    ("analyse", 1, "100000001", 200, "gs"),
    ("analyse", 1, "100000001", 500, "gs"),
    ("analyse", 2, "800000001", 11, "sr"),
    ("analyse", 2, "800000001", 12, "sr"),
    ("analyse", 2, "800000001", 1, "sr"),
    ("analyse", 9, "999999999", 301, "gs"),  # 无记录 → None
    ("stat", 1, "100000001", "char", "gs"),
    ("stat", 1, "100000001", "up", "gs"),
    ("stat", 1, "100000001", "normal", "gs"),
    ("stat", 1, "100000001", "mix", "gs"),
    ("stat", 1, "100000001", "all", "gs"),
    ("stat", 2, "800000001", "up", "sr"),
    ("stat", 2, "800000001", "char", "sr"),
    ("stat", 2, "800000001", "weapon", "sr"),
    ("stat", 2, "800000001", "normal", "sr"),
    ("stat", 2, "800000001", "all", "sr"),
    ("stat", 2, "800000001", "mix", "sr"),  # 星铁无集录池 → None
]


def main() -> None:
    out = {}
    for kind, user, uid, gtype, game in CASES:
        key = f"{kind}|{user}|{uid}|{gtype}|{game}"
        if kind == "analyse":
            out[key] = ga.analyse(user, uid, gtype, game)
        else:
            out[key] = ga.stat(user, uid, gtype, game)
    dst = Path(__file__).with_name("py_out.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"written {dst} ({len(out)} cases)")


if __name__ == "__main__":
    main()
