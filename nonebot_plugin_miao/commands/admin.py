"""管理指令：#更新面板资源（SUPERUSER）、#面板资源信息（公开）

对标 miao-plugin 的 #喵喵更新：运行时从上游仓库经 jsDelivr 拉取最新文本
元数据到本地覆盖目录，meta 加载器覆盖目录优先、打包资源兜底。
更新逻辑见 datasource/res_update.py。
"""
from __future__ import annotations

from nonebot import on_regex
from nonebot.permission import SUPERUSER

from ..datasource import res_update
from .common import guard

RE_RES_UPDATE = r"^#(更新面板资源|面板资源更新)$"
RE_RES_INFO = r"^#(面板资源信息|资源更新信息)$"

res_update_m = on_regex(RE_RES_UPDATE, permission=SUPERUSER, priority=5, block=True)
res_info_m = on_regex(RE_RES_INFO, priority=5, block=True)


@res_update_m.handle()
@guard(res_update_m)
async def _update():
    await res_update_m.send("开始更新面板资源，文件较多可能需要几十秒...")
    ret = await res_update.update_resources()
    if ret["code"] != "ok":
        await res_update_m.finish(f"面板资源更新失败：{ret.get('reason', '未知错误')}")
    await res_update_m.finish(
        "面板资源更新完成：\n"
        f"原神角色 {ret['gs_chars']} 个 / 星铁角色 {ret['sr_chars']} 个\n"
        f"卡池 {ret['pools']} 期，失败文件 {len(ret['failed'])} 个，耗时 {ret['duration']:.1f}s"
    )


@res_info_m.handle()
@guard(res_info_m)
async def _info():
    source = "覆盖版（#更新面板资源 拉取）" if res_update.using_override() else "打包版（插件内置）"
    lines = [f"面板资源来源：{source}"]
    info = res_update.last_update_info()
    if info:
        lines.append(
            f"上次更新：{info.get('time')}（成功 {info.get('ok')} 个文件，失败 {info.get('failed_count')} 个）"
        )
    else:
        lines.append("尚未执行过运行时更新，SUPERUSER 可发送 #更新面板资源")
    await res_info_m.finish("\n".join(lines))
