"""QuickJS runtime used by :mod:`nonebot_plugin_miao.dmg.service`.

The character ``calc.js`` files are the upstream miao-plugin rules.  Keeping
them as JavaScript is important: they contain hundreds of character-specific
closures and are updated alongside the metadata package.

The runtime itself is a vendored adaptation of the upstream damage engine
(``refs/miao-plugin/models/dmg/*.js`` + ``models/ProfileDmg.js``), stored as
separate files under ``dmg/js/`` and concatenated here:

- ``prelude.js``：手写 stub（lodash / Format / console）
- ``dmg_meta.js`` / ``dmg_attr.js`` / ``dmg_calc.js`` / ``dmg_buffs.js``：
  由原版 DmgCalcMeta / AttrItem+DmgMastery+DmgAttr / DmgCalc / DmgBuffs
  机械转换而来（仅删除 import/export，逻辑与原版逐行一致）
- ``engine.js``：手写编排（ProfileDmg.calcData 的 'dmg' 模式移植 +
  Meta/ArtifactSet/Weapon 依赖注入）
"""
from __future__ import annotations

from pathlib import Path

JS_DIR = Path(__file__).resolve().parent / "js"

_JS_FILES = ("prelude.js", "dmg_meta.js", "dmg_attr.js", "dmg_calc.js", "dmg_buffs.js", "engine.js")


def _load_runtime() -> str:
    return "\n".join((JS_DIR / name).read_text(encoding="utf-8") for name in _JS_FILES)


RUNTIME = _load_runtime()
