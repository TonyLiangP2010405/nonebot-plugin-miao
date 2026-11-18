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
    # 面板功能总开关（SUPERUSER 也可用 /面板开关 运行时切换）
    miao_panel_enabled: bool = True
    # 面板功能开放时段（整点，Asia/Shanghai）：08:00 开启，17:00 关闭
    # 两个值相同表示全天开放；支持跨天（如 22 到 6）
    miao_panel_open_hour: int = Field(default=8, ge=0, le=23)
    miao_panel_close_hour: int = Field(default=17, ge=0, le=23)
    # /更新面板、/米游社更新面板 的命令冷却（分钟，原神/星铁分别计算；0 表示不限制）
    miao_panel_update_interval: int = Field(default=60, ge=0)
    # 面板查看限流（与 nonebot-plugin-mantou-affection 联动）
    # 每人每小时免费查看次数（角色面板与群排名各自独立计算）
    miao_panel_free_per_hour: int = Field(default=1, ge=0)
    # 每人每小时最多查看次数（含免费与付费；转盘次数不占额度）
    miao_panel_user_hourly_limit: int = Field(default=2, ge=0)
    # 免费次数用完后，每次查看消耗的好感度（0 表示不允许用好感度抵扣）
    miao_panel_affection_cost: int = Field(default=10, ge=0)
    # 付费查看的确认有效期（秒）
    miao_panel_confirm_timeout: int = Field(default=30, ge=5, le=300)
    # 各游戏角色攻略图默认来源（可用“设置默认攻略”类指令持久化覆盖）
    miao_strategy_default_source: int = 1
    miao_sr_strategy_default_source: int = 1
    miao_zzz_strategy_default_source: int = 1
