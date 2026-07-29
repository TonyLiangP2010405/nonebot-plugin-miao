"""插件配置，全部带默认值，零配置可导入"""
from pydantic import BaseModel


class Config(BaseModel):
    # miao-plugin 静态资源镜像（原始资源包，按需下载）
    miao_res_mirror: str = (
        "https://cdn.jsdelivr.net/gh/yoimiya-kokomi/miao-plugin@master/resources"
    )
    # 面板更新冷却时间（分钟）
    miao_profile_interval: int = 3
    # 全局米游社 cookie（可选，供面板/抽卡分析接口使用）
    miao_mys_cookie: str = ""
    # 模拟抽卡每日十连次数限制
    miao_gacha_daily_limit: int = 1
    # 每日自动更新面板资源（凌晨 4:20，失败仅记日志）
    miao_res_auto_update: bool = True
