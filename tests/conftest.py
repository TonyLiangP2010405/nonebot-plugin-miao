"""pytest 全局配置：让 nonebug 使用无第三方依赖的 none 驱动初始化 nonebot"""
import pytest
from nonebug import NONEBOT_INIT_KWARGS


def pytest_configure(config: pytest.Config) -> None:
    config.stash[NONEBOT_INIT_KWARGS] = {"driver": "~none"}
