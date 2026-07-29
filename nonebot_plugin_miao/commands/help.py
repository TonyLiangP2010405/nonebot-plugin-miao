"""帮助指令。"""
from nonebot import on_regex

from .common import guard

profile_help_m = on_regex(r"^#面板帮助$", priority=5, block=True)
gacha_help_m = on_regex(r"^#抽卡帮助$", priority=5, block=True)


@profile_help_m.handle()
@guard(profile_help_m)
async def _profile_help():
    await profile_help_m.finish(
        "面板指令：\n"
        "#绑定uid <UID> / #星铁绑定uid <UID>\n"
        "#更新面板 / #星铁更新面板\n"
        "#面板列表 / #星铁面板列表\n"
        "#角色名面板 / #角色名圣遗物 / #角色名伤害[序号]\n"
        "#圣遗物列表 / #星铁遗器列表\n"
        "#米游社更新面板（需私聊 #绑定cookie <cookie>）"
    )


@gacha_help_m.handle()
@guard(gacha_help_m)
async def _gacha_help():
    await gacha_help_m.finish(
        "抽卡指令：\n"
        "发送含 authkey= 的抽卡链接，或 #更新抽卡记录\n"
        "#抽卡分析 / #角色池记录 / #全部池统计（星铁命令前加“星铁”）\n"
        "#十连 / #武器十连 / #常驻十连 / #单抽 / #定轨\n"
        "#导入记录（发送 UIGF/SRGF JSON 文件）/ #导出记录"
    )
