"""真实网络验证：跑一次 update_resources()（jsdelivr/GitHub 链路）"""
import asyncio
import os
import tempfile

os.environ["LOCALSTORE_DATA_DIR"] = tempfile.mkdtemp(prefix="miao_res_verify_")
print("LOCALSTORE_DATA_DIR =", os.environ["LOCALSTORE_DATA_DIR"])

import nonebot  # noqa: E402

nonebot.init(driver="~none")
nonebot.load_plugin("nonebot_plugin_miao")

from nonebot_plugin_miao.datasource import res_update  # noqa: E402


def on_progress(done, total, path):
    if done % 200 == 0 or done == total:
        print(f"  progress {done}/{total} {path}", flush=True)


async def main():
    # 先看文件树走的哪条链路
    try:
        files = await res_update._list_via_jsdelivr(res_update.MIAO_REPO)
        print("jsdelivr data API 可用，文件数:", len(files))
    except Exception as e:
        print("jsdelivr data API 失败:", type(e).__name__, e)
    files = await res_update.list_remote_files(res_update.MIAO_REPO)
    print("list_remote_files 文件总数:", len(files))
    selected = res_update.select_meta_files(files)
    print("筛选后元数据文件数:", len(selected))

    ret = await res_update.update_resources(on_progress=on_progress)
    print("update_resources 结果:")
    for k, v in ret.items():
        if k == "failed":
            print(f"  failed: {len(v)} 个 {v[:5]}")
        else:
            print(f"  {k}: {v}")

    # 覆盖目录验证：meta 走覆盖目录能查到角色
    from nonebot_plugin_miao.core import meta

    char = meta.get_character("刻晴", "gs")
    print("覆盖目录查角色 刻晴:", char.name if char else None)
    print("using_override:", res_update.using_override())
    print("last_update_info:", res_update.last_update_info())
    print("覆盖目录:", res_update.override_dir())


asyncio.run(main())
