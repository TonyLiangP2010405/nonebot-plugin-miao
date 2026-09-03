# nonebot-plugin-miao

原神 / 崩坏：星穹铁道的 NoneBot2 抽卡记录、模拟抽卡与角色面板插件，移植自 [miao-plugin](https://github.com/yoimiya-kokomi/miao-plugin)（MIT）。支持 OneBot V11。

## 功能

- Authkey 抽卡记录更新、分析、按版本统计、UIGF / SRGF 导入导出
- 原神与星铁模拟抽卡、保底、武器定轨和每日次数限制
- Enka（原神）、Mihomo（星铁）、米游社 Cookie 面板更新
- 属性计算、圣遗物 / 遗器评分、面板列表 / 详情 / 伤害图片
- 原神角色与武器图鉴、名称索引（无需绑定 UID）
- 原神角色攻略图（7 个来源、本地缓存、强制刷新与默认来源设置）

## 安装

```bash
nb plugin install nonebot-plugin-miao-genshin
pip install nonebot-plugin-miao-genshin
poetry add nonebot-plugin-miao-genshin
```

> 注意：PyPI 上的 `nonebot-plugin-miao` 是他人发布的无关项目，请勿安装；本插件的 PyPI 包名为 `nonebot-plugin-miao-genshin`（导入模块名仍为 `nonebot_plugin_miao`）。

## 配置

均有默认值；在 NoneBot `.env` 中按需覆盖。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `MIAO_RES_MIRROR` | jsDelivr miao-plugin resources | 角色图片与静态资源镜像 |
| `MIAO_PROFILE_INTERVAL` | `3` | 面板更新冷却（分钟） |
| `MIAO_MYS_COOKIE` | 空 | 全局米游社 Cookie（可选） |
| `MIAO_GACHA_DAILY_LIMIT` | `1` | 每日模拟十连次数 |
| `MIAO_RES_AUTO_UPDATE` | `True` | 每日自动更新面板资源（凌晨 4:20，需 nonebot-plugin-apscheduler） |
| `MIAO_STRATEGY_DEFAULT_SOURCE` | `1` | 原神角色攻略图默认来源（1-7，可被设置指令覆盖） |

## 指令

| 指令 | 范围 | 说明 |
| --- | --- | --- |
| `/绑定uid <UID>` / `/星铁绑定uid <UID>` | 群聊、私聊 | 绑定游戏 UID |
| `/绑定cookie <cookie>` | 仅私聊 | 绑定米游社 Cookie |
| `/更新抽卡记录` / `/抽卡分析` / `/全部池统计` | 群聊、私聊 | 抽卡记录与统计；星铁命令前加“星铁” |
| `十连` / `武器十连` / `单抽` | 群聊、私聊 | 模拟抽卡；唯一无需 `/` 前缀的功能 |
| `/定轨` | 群聊、私聊 | 模拟武器池定轨 |
| `/导入记录` / `/导出记录` | 群聊、私聊 | UIGF / SRGF 记录交换 |
| `/更新面板` / `/米游社更新面板` | 群聊、私聊 | 更新角色面板 |
| `/面板列表` / `/角色名面板` / `/角色名伤害2` | 群聊、私聊 | 查看面板与指定伤害条目 |
| `/角色图鉴` / `/武器图鉴` | 群聊、私聊 | 查看原神角色或武器名称索引 |
| `/芙宁娜图鉴` / `/雾切图鉴` | 群聊、私聊 | 查看角色或武器的详细图鉴；也支持 `#` 前缀 |
| `/心海攻略[1-7]` | 群聊、私聊 | 查看原神角色攻略图；末尾数字选择攻略来源 |
| `/更新心海攻略[1-7]` | 群聊、私聊 | 忽略本地缓存并重新获取攻略图 |
| `/攻略帮助` / `/设置默认攻略[1-7]` | 群聊、私聊 | 查看来源或持久化设置默认来源 |
| `/圣遗物列表` / `/面板帮助` / `/抽卡帮助` | 群聊、私聊 | 列表与帮助 |

除模拟抽卡外，所有功能均要求以 `/` 开头；图鉴和攻略指令额外兼容 Yunzai 常用的 `#` 前缀。发送 Authkey 抽卡链接时，也需要在完整链接前添加 `/`。

攻略来源编号：1 西风驿站、2 原神观测枢、3 派蒙喵喵屋、4 OH是姜姜呀、5 曉K、6 坤易、7 婧枫赛赛（角色配队一图流）。攻略数据来自米游社公开合集；接口不可用或对应来源未收录角色时，插件会返回中文提示。

## 移植对照

| miao-plugin 功能 | 本插件位置 | 状态 |
| --- | --- | --- |
| 抽卡记录、分析、模拟 | `gacha/`、`datasource/gacha_log.py` | 已实现 |
| Enka / Mihomo / 米游社面板 | `datasource/` | 已实现 |
| 属性、评分与伤害规则 | `core/`、`dmg/` | 已实现 |
| 面板和抽卡卡片 | `render/` | 已实现 |
| 原神角色与武器图鉴 | `commands/encyclopedia.py`、`render/encyclopedia.py` | 已实现 |
| 原神角色攻略图 | `commands/strategy.py`、`datasource/strategy.py` | 已实现（7 来源、缓存、刷新、默认来源） |

未移植：群内排名、练度统计、面板图上传、私有 API、备选面板源与 Yunzai 按钮交互。

## 开发

```bash
poetry install
poetry run python -m compileall nonebot_plugin_miao
poetry run pytest -v
poetry run ruff check .
```

## 致谢与许可

- [miao-plugin](https://github.com/yoimiya-kokomi/miao-plugin)（MIT）
- [Yunzai-genshin](https://github.com/TimeRainStarSky/Yunzai-genshin)

本项目使用 [MIT License](LICENSE)。
