"""
NoneBot2 原神/星铁抽卡分析与角色面板插件
移植自 Yunzai 的 miao-plugin（yoimiya-kokomi/miao-plugin, MIT）
"""
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="喵喵抽卡面板",
    description="原神/星铁抽卡分析与角色面板（移植自 miao-plugin）",
    usage=(
        "#抽卡分析          原神抽卡记录分析\n"
        "#星铁抽卡分析      星铁抽卡记录分析\n"
        "#模拟抽卡          模拟十连抽卡\n"
        "#面板 <角色>       查看角色面板\n"
        "#更新面板          更新角色面板数据"
    ),
    type="application",
    homepage="https://github.com/TonyLiangP2010405/nonebot-plugin-miao",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

from .commands import bind, gacha, help, profile  # noqa: E402,F401 注册指令 matcher
