"""伤害计算（QuickJS 执行上游 miao-plugin 角色规则）。"""

from .service import DamageError, calc_dmg

__all__ = ("DamageError", "calc_dmg")
