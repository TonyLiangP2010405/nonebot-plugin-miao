"""插件配置，全部带默认值，零配置可导入"""
from pydantic import BaseModel, Field


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
    # 三游戏官方模拟卡池后台同步/按需检查间隔（分钟）
    miao_gacha_pool_interval: int = Field(default=60, ge=1)
    # 每日自动更新面板资源（凌晨 4:20，失败仅记日志）
    miao_res_auto_update: bool = True
    # 面板查看限流（与 nonebot-plugin-mantou-affection 联动）
    # 每个群每小时最多查看几次角色面板（SUPERUSER 不受限）
    miao_panel_group_hourly_limit: int = Field(default=5, ge=0)
    # 面板次数用完后，每次查看消耗的好感度（0 表示不允许用好感度抵扣）
    miao_panel_affection_cost: int = Field(default=10, ge=0)
    # 各游戏角色攻略图默认来源（可用“设置默认攻略”类指令持久化覆盖）
    miao_strategy_default_source: int = 1
    miao_sr_strategy_default_source: int = 1
    miao_zzz_strategy_default_source: int = 1
