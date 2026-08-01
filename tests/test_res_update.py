"""运行时元数据更新测试（datasource/res_update + core/meta 覆盖优先级）

用 respx mock jsDelivr 的 data API（文件树）与 CDN（文件内容），构造小型假
元数据仓库：2 个 gs 角色、1 个 sr 角色、pool.js、3 个卡池 yaml（其一故意 404
验证失败记录），覆盖目录通过 monkeypatch 重定向到 tmp_path。

注意：tests/test_load.py 会清 sys.modules 后按插件重新加载本包，顶层 import
会拿到被替换掉的旧模块对象，因此 meta / res_update 一律在 fixture / 测试函数内
导入（与 test_commands.py 的做法一致）。
"""
import json
import re
import sys
import urllib.parse

import httpx
import pytest
import respx

MIAO_REPO = "yoimiya-kokomi/miao-plugin"
YUNZAI_REPO = "TimeRainStarSky/Yunzai-genshin"
MIAO_API = f"https://data.jsdelivr.com/v1/packages/gh/{MIAO_REPO}@master"

CHAR_A = {"id": 99000001, "name": "测试角色", "abbr": "测角", "star": 5, "elem": "pyro", "weapon": "sword"}
CHAR_B = {"id": 99000002, "name": "样例角色", "star": 4, "elem": "hydro", "weapon": "bow"}
SR_CHAR = {"id": 9901, "name": "测试星铁", "star": 5, "weapon": "虚无"}

# pool.js 含 poolDetail（比 yaml 数据新）→ update_resources 会用它派生 gacha-sim/pool.json
POOL_JS = """
export const poolName = {}
export const poolDetail = [{
  version: '6.7',
  half: '下半',
  from: '2026-07-21 18:00:00',
  to: '2026-08-11 14:59:59',
  char5: ['测试角色', '样例角色'],
  char4: ['甲', '乙', '丙'],
  weapon5: ['武器甲', '武器乙'],
  weapon4: ['丙丁']
}]
"""

# pool.js 派生出的旧版格式卡池（倒序，最新在前）
DERIVED_POOLS = [{
    "up4": ["甲", "乙", "丙"],
    "up5": ["测试角色"],
    "up5_2": ["样例角色"],
    "weapon5": ["武器甲", "武器乙"],
    "weapon4": ["丙丁"],
    "endTime": "2026-08-11 14:59:59",
}]

# CDN 实际存在的文件（alias.js 故意缺失 → 404 → 进入 failed 列表）
FILES = {
    "resources/meta-gs/character/测试角色/data.json": json.dumps(CHAR_A, ensure_ascii=False),
    "resources/meta-gs/character/样例角色/data.json": json.dumps(CHAR_B, ensure_ascii=False),
    "resources/meta-gs/info/pool.js": POOL_JS,
    "resources/meta-sr/character/测试星铁/data.json": json.dumps(SR_CHAR, ensure_ascii=False),
    "resources/meta-sr/info/index.js": "export const poolNameSr = []",
}

# gacha.yaml 故意带 BOM，验证 utf-8-sig 处理
YUNZAI_FILES = {
    "defSet/gacha/gacha.yaml": ("gacha:\n  - 角色活动祈愿\n", "utf-8-sig"),
    "defSet/gacha/pool.yaml": ("- up5:\n  - 测试角色\n- up5:\n  - 样例角色\n", "utf-8"),
    "defSet/gacha/set.yaml": ("role:\n  - 测试角色\n", "utf-8"),
}


def _file(name):
    return {"type": "file", "name": name, "size": 1}


def _dir(name, files):
    return {"type": "directory", "name": name, "files": files}


# 假文件树：含应被筛选排除的 imgs/ 目录、图片与 meta 之外的文件
MIAO_TREE = {
    "files": [
        _dir("resources", [
            _dir("meta-gs", [
                _dir("character", [
                    _dir("测试角色", [_file("data.json"), _dir("imgs", [_file("face.webp")])]),
                    _dir("样例角色", [_file("data.json")]),
                    _file("alias.js"),
                ]),
                _dir("info", [_file("pool.js")]),
            ]),
            _dir("meta-sr", [
                _dir("character", [_dir("测试星铁", [_file("data.json")])]),
                _dir("info", [_file("index.js")]),
            ]),
            _dir("gacha", [_dir("imgs", [_file("no-avatar.webp")])]),
        ]),
        _file("package.json"),
    ]
}


def _modules():
    """在 nonebot 初始化后导入被测模块（test_load.py 重载包后仍拿到当前模块对象）"""
    from nonebot_plugin_miao.core import meta
    from nonebot_plugin_miao.datasource import res_update

    return meta, res_update


@pytest.fixture
def mock_jsdelivr():
    with respx.mock(assert_all_called=False) as router:
        router.get(MIAO_API).mock(return_value=httpx.Response(200, json=MIAO_TREE))

        def cdn(request: httpx.Request) -> httpx.Response:
            rel = urllib.parse.unquote(request.url.path).split("@master/", 1)[1]
            if rel in FILES:
                return httpx.Response(200, content=FILES[rel].encode("utf-8"))
            if rel in YUNZAI_FILES:
                text, enc = YUNZAI_FILES[rel]
                return httpx.Response(200, content=text.encode(enc))
            return httpx.Response(404)

        router.route(host="cdn.jsdelivr.net").mock(side_effect=cdn)
        yield router


@pytest.fixture
def override_dir(tmp_path, monkeypatch):
    """覆盖目录重定向到 tmp_path；测试结束清空 meta 缓存，避免污染其他用例"""
    meta, res_update = _modules()
    d = tmp_path / "meta_override"
    monkeypatch.setattr(res_update, "override_dir", lambda: d)
    yield d
    meta.clear_cache()


@pytest.fixture
def low_min_chars(monkeypatch):
    """假仓库只有 3 个角色，降低关键文件校验阈值"""
    _, res_update = _modules()
    monkeypatch.setattr(res_update, "MIN_TOTAL_CHARS", 2)


# ---------------------------------------------------------------------------
# 文件树与筛选
# ---------------------------------------------------------------------------


async def test_list_remote_files(mock_jsdelivr):
    _, res_update = _modules()
    files = await res_update.list_remote_files(MIAO_REPO)
    assert "resources/meta-gs/character/测试角色/data.json" in files
    assert "resources/meta-sr/info/index.js" in files
    assert "package.json" in files
    # 文件树原样包含图片，由筛选环节排除
    assert "resources/meta-gs/character/测试角色/imgs/face.webp" in files


async def test_list_remote_files_github_fallback():
    """jsDelivr 对超 50MB 包返回 403，回退 GitHub trees API（真实环境 miao-plugin 即如此）"""
    _, res_update = _modules()
    gh_tree = {
        "tree": [
            {"path": "resources/meta-gs/character/刻晴/data.json", "type": "blob"},
            {"path": "resources/meta-gs", "type": "tree"},
            {"path": "package.json", "type": "blob"},
        ],
        "truncated": False,
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(MIAO_API).mock(return_value=httpx.Response(403, json={"status": 403}))
        router.get(f"https://api.github.com/repos/{MIAO_REPO}/git/trees/master?recursive=1").mock(
            return_value=httpx.Response(200, json=gh_tree)
        )
        files = await res_update.list_remote_files(MIAO_REPO)
    assert files == ["resources/meta-gs/character/刻晴/data.json", "package.json"]


def test_select_meta_files():
    _, res_update = _modules()
    files = [
        "resources/meta-gs/character/测试角色/data.json",
        "resources/meta-gs/character/测试角色/imgs/face.webp",  # imgs 目录
        "resources/meta-gs/character/测试角色/icons/cons-1.webp",  # icons 目录
        "resources/meta-gs/character/alias.js",
        "resources/meta-sr/info/index.js",
        "resources/meta-gs/info/splash/xx.json",  # splash 目录
        "resources/gacha/imgs/no-avatar.webp",  # 非 meta-gs/meta-sr
        "resources/fonts/HYWH-65W.woff",  # 非 json/js
        "package.json",  # 非 resources/meta-*
    ]
    selected = res_update.select_meta_files(files)
    assert selected == [
        "resources/meta-gs/character/alias.js",
        "resources/meta-gs/character/测试角色/data.json",
        "resources/meta-sr/info/index.js",
    ]


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


async def test_download_files(tmp_path, mock_jsdelivr):
    _, res_update = _modules()
    progress = []
    ret = await res_update.download_files(
        MIAO_REPO,
        ["resources/meta-gs/info/pool.js", "resources/meta-gs/character/alias.js"],
        tmp_path,
        on_progress=lambda done, total, path: progress.append((done, total, path)),
    )
    assert ret["ok"] == 1
    assert ret["failed"] == ["resources/meta-gs/character/alias.js"]
    assert (tmp_path / "resources/meta-gs/info/pool.js").read_text() == POOL_JS
    assert progress == [(1, 2, "resources/meta-gs/info/pool.js")]


# ---------------------------------------------------------------------------
# 编排：成功更新 + 覆盖优先级
# ---------------------------------------------------------------------------


async def test_update_resources_success(override_dir, mock_jsdelivr, low_min_chars):
    _, res_update = _modules()
    ret = await res_update.update_resources()
    assert ret["code"] == "ok"
    assert ret["gs_chars"] == 2
    assert ret["sr_chars"] == 1
    assert ret["pools"] == 1
    assert ret["failed"] == ["resources/meta-gs/character/alias.js"]
    assert ret["duration"] >= 0

    # 落盘结构：meta-gs/meta-sr + yaml 转出的 gacha-sim/*.json
    assert (override_dir / "meta-gs/info/pool.js").is_file()
    assert (override_dir / "meta-sr/character/测试星铁/data.json").is_file()
    # pool.json 由 pool.js 派生（比 yaml 数据新）
    pools = json.loads((override_dir / "gacha-sim/pool.json").read_text(encoding="utf-8"))
    assert pools == DERIVED_POOLS
    # gacha.yaml 带 BOM，utf-8-sig 读取后正常解析
    gacha = json.loads((override_dir / "gacha-sim/gacha.json").read_text(encoding="utf-8"))
    assert gacha == {"gacha": ["角色活动祈愿"]}
    # 临时目录已清理
    assert not (override_dir.parent / ".meta_override.work").exists()
    # 上次更新信息已记录
    info = res_update.last_update_info()
    assert info and info["ok"] == len(FILES) + len(YUNZAI_FILES) and info["failed_count"] == 1


async def test_override_priority_and_clear_cache(override_dir, mock_jsdelivr, low_min_chars):
    meta, res_update = _modules()
    # 更新前：打包资源，能查到刻晴，查不到假角色
    assert meta.get_character("测试角色", "gs") is None
    assert meta.get_character("刻晴", "gs") is not None

    ret = await res_update.update_resources()
    assert ret["code"] == "ok"

    # update_resources 内部已 clear_cache：覆盖目录优先，假角色可查
    char = meta.get_character("测试角色", "gs")
    assert char is not None and char.id == 99000001
    # 别名（abbr）同样生效
    assert meta.get_character("测角", "gs").name == "测试角色"
    # 覆盖目录整体替换打包资源：刻晴不在覆盖目录里，查不到
    assert meta.get_character("刻晴", "gs") is None
    # gacha_sim_config 也走覆盖目录（pool 为 pool.js 派生数据）
    sim = meta.gacha_sim_config()
    assert sim["pool"] == DERIVED_POOLS


async def test_pool_js_fallback_to_yaml(override_dir, low_min_chars):
    """pool.js 无法解析（无 poolDetail）时回退 yaml 转换的 pool.json"""
    _, res_update = _modules()
    bad_files = {**FILES, "resources/meta-gs/info/pool.js": "export const poolName = []"}
    with respx.mock(assert_all_called=False) as router:
        router.get(MIAO_API).mock(return_value=httpx.Response(200, json=MIAO_TREE))

        def cdn(request: httpx.Request) -> httpx.Response:
            rel = urllib.parse.unquote(request.url.path).split("@master/", 1)[1]
            if rel in bad_files:
                return httpx.Response(200, content=bad_files[rel].encode("utf-8"))
            if rel in YUNZAI_FILES:
                text, enc = YUNZAI_FILES[rel]
                return httpx.Response(200, content=text.encode(enc))
            return httpx.Response(404)

        router.route(host="cdn.jsdelivr.net").mock(side_effect=cdn)
        ret = await res_update.update_resources()
    assert ret["code"] == "ok"
    pools = json.loads((override_dir / "gacha-sim/pool.json").read_text(encoding="utf-8"))
    assert pools == [{"up5": ["测试角色"]}, {"up5": ["样例角色"]}]


# ---------------------------------------------------------------------------
# pool.js → gacha-sim/pool.json 派生
# ---------------------------------------------------------------------------


def test_pools_from_pool_js(tmp_path):
    _, res_update = _modules()
    p = tmp_path / "pool.js"
    p.write_text("""
export const poolName = {}
export const poolDetail = [{
  version: '1.0', half: '上半',
  from: '2020-09-28 06:00:00', to: '2020-10-18 17:59:59',
  char5: ['温迪'], char4: ['芭芭拉'],
  weapon5: ['风鹰剑'], weapon4: ['笛剑']
}, {
  version: '6.7', half: '下半',
  from: '2026-07-21 18:00:00', to: '2026-08-11 14:59:59',
  char5: ['哥伦比娅', '雷电将军'], char4: ['雅珂达'],
  weapon5: ['帷间夜曲', '薙草之稻光'], weapon4: ['祭礼剑']
}]
export const mixPoolDetail = []
""", encoding="utf-8")
    pools = res_update.pools_from_pool_js(p)
    # 按 endTime 倒序，最新在前
    assert [x["endTime"] for x in pools] == ["2026-08-11 14:59:59", "2020-10-18 17:59:59"]
    newest = pools[0]
    assert newest["up5"] == ["哥伦比娅"]
    assert newest["up5_2"] == ["雷电将军"]
    assert newest["weapon5"] == ["帷间夜曲", "薙草之稻光"]
    # 单 up 期 up5_2 复用 up5
    assert pools[1]["up5"] == pools[1]["up5_2"] == ["温迪"]


def test_pools_from_pool_js_invalid(tmp_path):
    _, res_update = _modules()
    p = tmp_path / "pool.js"
    p.write_text("export const poolName = []", encoding="utf-8")
    with pytest.raises(Exception):
        res_update.pools_from_pool_js(p)


# ---------------------------------------------------------------------------
# 原子性：关键文件校验不通过时不替换旧覆盖目录
# ---------------------------------------------------------------------------


async def test_atomic_keep_old_on_validation_failure(override_dir, mock_jsdelivr):
    _, res_update = _modules()
    # 预置旧覆盖目录（含 meta-gs 使其被 _res_root 认可）
    old_char = override_dir / "meta-gs/character/旧角色"
    old_char.mkdir(parents=True)
    (old_char / "data.json").write_text('{"id": 1, "name": "旧角色"}', encoding="utf-8")

    # 不降低阈值：假仓库 3 个角色 < MIN_TOTAL_CHARS(100)，校验失败
    ret = await res_update.update_resources()
    assert ret["code"] == "error"
    assert "不完整" in ret["reason"]
    # 旧覆盖目录原样保留，未记录更新时间
    assert (old_char / "data.json").is_file()
    assert res_update.last_update_info() is None
    assert not (override_dir.parent / ".meta_override.work").exists()


# ---------------------------------------------------------------------------
# 命令注册冒烟 + scheduler 注册
# ---------------------------------------------------------------------------


def test_admin_matchers_registered():
    from nonebot.matcher import matchers

    from nonebot_plugin_miao.commands import admin

    all_matchers = set()
    for ms in matchers.values():
        all_matchers.update(ms)
    assert admin.res_update_m in all_matchers
    assert admin.res_info_m in all_matchers
    assert re.match(admin.RE_RES_UPDATE, "/更新面板资源")
    assert re.match(admin.RE_RES_INFO, "/面板资源信息")
    assert not re.match(admin.RE_RES_UPDATE, "更新面板资源")
    assert not re.match(admin.RE_RES_UPDATE, "#更新面板资源")
    assert not re.match(admin.RE_RES_INFO, "面板资源信息")
    assert not re.match(admin.RE_RES_INFO, "#面板资源信息")


def test_plugin_load_with_scheduler():
    """插件加载不崩，apscheduler 每日任务已注册（可能已被 test_load.py 加载过）"""
    import nonebot

    if nonebot.get_plugin("nonebot_plugin_miao") is None:
        for mod in [m for m in sys.modules if m == "nonebot_plugin_miao" or m.startswith("nonebot_plugin_miao.")]:
            sys.modules.pop(mod)
        assert nonebot.load_plugin("nonebot_plugin_miao") is not None

    import nonebot_plugin_miao

    scheduler = nonebot_plugin_miao.scheduler
    assert scheduler is not None
    job_ids = {j.id for j in scheduler.get_jobs()}
    # scheduler 未启动时任务在 pending 列表（(job, jobstore, replace_existing) 元组）
    job_ids |= {t[0].id for t in getattr(scheduler, "_pending_jobs", [])}
    assert "miao_res_auto_update" in job_ids
