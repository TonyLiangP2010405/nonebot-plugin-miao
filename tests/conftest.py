"""pytest 全局配置：让 nonebug 使用无第三方依赖的 none 驱动初始化 nonebot"""
import pytest
from nonebug import NONEBOT_INIT_KWARGS


def pytest_configure(config: pytest.Config) -> None:
    config.stash[NONEBOT_INIT_KWARGS] = {"driver": "~none"}


@pytest.fixture
def official_pools():
    """2026-09-09 官方国服公开卡池录制，移除长说明正文。"""
    import json
    from pathlib import Path

    return {g: json.loads((Path(__file__).parent / "fixtures" / "sim_pools" / f"{g}.json").read_text())
            for g in ("gs", "sr", "zzz")}


@pytest.fixture
def sim_data(tmp_path, monkeypatch, official_pools, request):
    from nonebot_plugin_miao.core import store
    from nonebot_plugin_miao.datasource import sim_pools

    monkeypatch.setattr(store, "_data_dir", lambda: tmp_path)
    # 加载冒烟会重新导入插件；收集时已持有旧模块的测试也必须指向临时目录。
    monkeypatch.setattr(getattr(request.module, "store", store), "_data_dir", lambda: tmp_path)
    snapshots = {}
    for game, data in official_pools.items():
        pools = [sim_pools.parse_pool(game, row, data["details"][row["gacha_id"]])
                 for row in data["list"]["data"]["list"]]
        pools.sort(key=lambda p: p["group"].startswith("collab"))
        for pool in pools:
            # 模拟器测试固定有效区间，解析/真实日期过滤在数据层测试覆盖。
            pool.update(start=0, end=4102444800)
        snapshot = {"schema": 1, "fetchedAt": 1, "pools": pools}
        store.save_json(sim_pools.cache_path(game), snapshot)
        snapshots[game] = snapshot
    return snapshots
