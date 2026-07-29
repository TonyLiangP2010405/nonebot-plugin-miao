"""插件加载冒烟测试（nonebot 初始化由 nonebug 的 autouse fixture 完成，见 conftest.py）"""
import sys

import nonebot


def test_plugin_load():
    # 其他测试模块（test_meta/test_store）在收集阶段已 import 过包本身，
    # nonebot.load_plugin 要求模块未被提前 import，这里先从 sys.modules 移除再按插件加载
    for mod in [m for m in sys.modules if m == "nonebot_plugin_miao" or m.startswith("nonebot_plugin_miao.")]:
        sys.modules.pop(mod)
    plugin = nonebot.load_plugin("nonebot_plugin_miao")
    assert plugin is not None
