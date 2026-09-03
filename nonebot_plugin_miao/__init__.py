"""
NoneBot2 原神/星铁抽卡分析与角色面板插件
移植自 Yunzai 的 miao-plugin（yoimiya-kokomi/miao-plugin, MIT）
"""
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="喵喵抽卡面板",
    description="原神/星铁抽卡分析、角色面板与原神角色武器图鉴（移植自 miao-plugin）",
    usage=(
        "/抽卡分析          原神抽卡记录分析\n"
        "/星铁抽卡分析      星铁抽卡记录分析\n"
        "十连               模拟十连抽卡（无需前缀）\n"
        "/<角色>面板        查看角色面板\n"
        "/更新面板          更新角色面板数据\n"
        "/<名称>图鉴        查看原神角色或武器图鉴"
    ),
    type="application",
    homepage="https://github.com/TonyLiangP2010405/nonebot-plugin-miao",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

# 每日自动更新面板资源。pytest 收集阶段直接 import 包时 nonebot 尚未初始化，
# require 会失败，此时跳过定时任务注册（真实运行 / nonebot.load_plugin 时正常注册）。
from nonebot import logger, require  # noqa: E402

from .commands import admin, bind, encyclopedia, gacha, help, profile  # noqa: E402,F401 注册指令 matcher

try:
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler
except (RuntimeError, ValueError, ImportError):
    scheduler = None

if scheduler is not None:

    @scheduler.scheduled_job("cron", hour=4, minute=20, id="miao_res_auto_update")
    async def _miao_res_auto_update() -> None:
        """每日 04:20 静默更新面板资源（错开整点），不给用户发消息，失败仅记日志"""
        from nonebot import get_plugin_config

        from .datasource.res_update import update_resources

        if not get_plugin_config(Config).miao_res_auto_update:
            return
        try:
            ret = await update_resources()
        except Exception as e:
            logger.warning(f"[miao] 每日面板资源自动更新失败: {e}")
            return
        if ret.get("code") != "ok":
            logger.warning(f"[miao] 每日面板资源自动更新失败: {ret.get('reason', '未知错误')}")
        elif ret.get("failed"):
            logger.warning(f"[miao] 每日面板资源自动更新完成，{len(ret['failed'])} 个文件下载失败")
