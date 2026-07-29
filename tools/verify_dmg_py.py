"""交叉验证 Python 侧：attr_cases.json 中的用例 → calc_dmg → tools/py_dmg_out.json

由 tools/verify_dmg.mjs 调用（也可单独跑）。用例与 verify_attr 共用
（优菈/迪卢克/镜流Pro），attr_cases.json 缺失时先跑 verify_attr_py 生成。
"""
import json
from pathlib import Path

from nonebot_plugin_miao.dmg import DamageError, calc_dmg

ROOT = Path(__file__).resolve().parent


def _row(ds: dict) -> dict:
    return {"title": ds.get("title"), "dmg": ds.get("dmg"), "avg": ds.get("avg")}


def _run(label: str, game: str, avatar: dict) -> dict:
    result = calc_dmg(avatar, game)
    rows = result["dmgData"]
    out = {
        "rows": [_row(ds) for ds in rows],
        "selectedIdx": result.get("selectedIdx"),
        "selected": _row(result["selected"]) if result.get("selected") else None,
        "msg": result.get("dmgMsg") or [],
        "dmgRet": result.get("dmgRet") or [],
        "byIdx": {},
        "idxOverflowError": None,
    }
    for idx in (1, 2):
        if idx > len(rows):
            continue
        out["byIdx"][str(idx)] = _row(calc_dmg(avatar, game, idx)["selected"])
    try:
        calc_dmg(avatar, game, len(rows) + 1)
    except DamageError as exc:
        out["idxOverflowError"] = str(exc)
    return out


def main() -> None:
    cases_file = ROOT / "attr_cases.json"
    if not cases_file.is_file():
        from verify_attr_py import main as gen_cases

        gen_cases()
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    out = {label: _run(label, c["game"], c["avatar"]) for label, c in cases.items()}
    (ROOT / "py_dmg_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"py dmg out: {', '.join(out)}")


if __name__ == "__main__":
    main()
