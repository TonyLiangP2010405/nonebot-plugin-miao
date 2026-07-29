"""交叉验证 Python 侧：解析 fixture → calc_attr/calc_mark → 输出 cases 与 Python 结果

由 tools/verify_attr.mjs 调用（也可单独跑）：
- tools/attr_cases.json：miao 格式 avatar（enka/mihomo fixture 的解析结果），供 mjs 构造同款输入
- tools/py_attr_out.json：Python calc_attr / calc_mark 结果
"""
import json
from pathlib import Path

from nonebot_plugin_miao.core.artis_mark import calc_mark
from nonebot_plugin_miao.core.attr_calc import calc_attr
from nonebot_plugin_miao.datasource.enka import parse_enka
from nonebot_plugin_miao.datasource.mihomo import parse_mihomo

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT.parent / "tests" / "fixtures"

# (用例名, game, 角色id)：原神 优菈/迪卢克/刻晴 + 星铁 镜流Pro/瓦尔特Pro（加强角色，attr 为数组形态）
CASES = [
    ("eula", "gs", "10000051"),
    ("diluc", "gs", "10000016"),
    ("keqing", "gs", "10000042"),
    ("jingliu", "sr", "2212"),
    ("welt", "sr", "2004"),
]

# 对比用的面板属性 key（staticAttr 嵌套结构不直接对比，逐 key 比 computed 值）
ATTR_KEYS = {
    "gs": ["hp", "atk", "def", "mastery", "cpct", "cdmg", "recharge", "dmg", "phy", "heal", "shield",
           "coloringDmg", "hpBase", "atkBase", "defBase"],
    "sr": ["hp", "atk", "def", "speed", "cpct", "cdmg", "recharge", "dmg", "heal", "stance",
           "effPct", "effDef", "joy", "hpBase", "atkBase", "defBase", "speedBase"],
}


def main() -> None:
    enka = parse_enka(json.loads((FIXTURES / "enka_800055548.json").read_text(encoding="utf-8")), "800055548")
    mihomo = parse_mihomo(json.loads((FIXTURES / "mihomo_702762444.json").read_text(encoding="utf-8")), "702762444")
    pools = {"gs": enka["avatars"], "sr": mihomo["avatars"]}

    cases: dict = {}
    out: dict = {}
    for label, game, cid in CASES:
        avatar = pools[game][cid]
        cases[label] = {"game": game, "avatar": avatar}
        attr = calc_attr(avatar, game)
        mark = calc_mark(avatar, attr, game)
        out[label] = {
            "attr": {k: attr.get(k) for k in ATTR_KEYS[game]},
            "mark": {
                "mark": mark["mark"],
                "markClass": mark["markClass"],
                "classTitle": mark["classTitle"],
                "charWeight": mark["charWeight"],
                "artis": {str(i): {"mark": a["mark"], "markClass": a["markClass"]} for i, a in mark["artis"].items()},
            },
        }
    (ROOT / "attr_cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    (ROOT / "py_attr_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"py out: {', '.join(out)}")


if __name__ == "__main__":
    main()
