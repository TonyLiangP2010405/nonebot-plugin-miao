"""帮助指令。"""
from nonebot import on_regex

from .common import guard

RE_PROFILE_HELP = r"^/面板帮助$"
RE_GACHA_HELP = r"^/抽卡帮助$"

profile_help_m = on_regex(RE_PROFILE_HELP, priority=5, block=True)
gacha_help_m = on_regex(RE_GACHA_HELP, priority=5, block=True)


@profile_help_m.handle()
@guard(profile_help_m)
async def _profile_help():
    await profile_help_m.finish(
        "面板指令：\n"
        "/绑定uid <UID> / /星铁绑定uid <UID>\n"
        "/更新面板 / /星铁更新面板\n"
        "/面板列表 / /星铁面板列表\n"
        "/角色名面板 / /角色名圣遗物 / /角色名伤害[序号]\n"
        "/圣遗物列表 / /星铁遗器列表\n"
        "/角色图鉴 / /武器图鉴 / /角色名图鉴 / /武器名图鉴\n"
        "/角色名攻略[1-7] / /星铁角色名攻略[1-3] / /绝区零角色名攻略[1-4]\n"
        "/攻略帮助 / /星铁攻略帮助 / /绝区零攻略帮助\n"
        "/米游社更新面板（需私聊 /绑定cookie <cookie>）"
    )


@gacha_help_m.handle()
@guard(gacha_help_m)
async def _gacha_help():
    await gacha_help_m.finish(
        "抽卡指令：\n"
        "在含 authkey= 的抽卡链接前加 /，或发送 /更新抽卡记录\n"
        "/抽卡分析 / /角色池记录 / /全部池统计（星铁命令前加“星铁”）\n"
        "模拟抽卡（三游戏，无需绑定 UID，无需前缀）：\n"
        "/卡池列表 / /星铁卡池列表 / /绝区零卡池列表\n"
        "十连 / 十连2 / 武器十连 / 常驻十连 / 单抽\n"
        "星铁十连 / 星铁十连2 / 星铁光锥十连 / 星铁光锥十连2\n"
        "绝区零十连 / 绝区零十连2 / 绝区零音擎十连 / 绝区零音擎十连2\n"
        "星铁常驻十连 / 绝区零常驻十连；十连可换成单抽或十抽\n"
        "编号以各游戏卡池列表为准；同游戏普通角色池共享保底，各游戏独立额度\n"
        "/定轨1 / /定轨2 / /定轨0（原神武器，0 取消）\n"
        "卡池自动同步，手动刷新：/更新卡池 / /更新星铁卡池 / /更新绝区零卡池\n"
        "/导入记录（发送 UIGF/SRGF JSON 文件）/ /导出记录"
    )
