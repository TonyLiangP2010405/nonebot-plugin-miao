"""JS 引擎后端抽象：quickjs 优先，pythonmonkey 兜底。

quickjs 没有 cp314+ 的预编译 wheel（Windows/Python 3.14 无 MSVC 环境会安装失败），
pythonmonkey（SpiderMonkey）覆盖 cp39~cp314 全平台 wheel，作为默认兜底后端。
两者都只暴露 eval 接口：执行代码并返回最后一个表达式的值（调用方约定用
JSON.stringify 收尾，因此返回值为 str）。
"""
from __future__ import annotations

import json
import os

try:
    import quickjs
except ImportError:
    quickjs = None

try:
    import pythonmonkey as pm
except ImportError:
    pm = None

_env_backend = os.environ.get("MIAO_JS_BACKEND", "").strip().lower()
if _env_backend in ("quickjs", "pythonmonkey"):
    _BACKEND = _env_backend  # 环境变量强制指定后端（调试用）
elif quickjs is not None:
    _BACKEND = "quickjs"
elif pm is not None:
    _BACKEND = "pythonmonkey"
else:
    _BACKEND = None


def backend() -> str | None:
    """当前可用的 JS 引擎名（None 表示两个后端都没装）。"""
    return _BACKEND


def eval_js(code: str) -> str:
    """在 JS 引擎中执行 code，返回最后表达式的值（调用方以 JSON.stringify 收尾）。"""
    if _BACKEND == "quickjs" and quickjs is not None:
        return quickjs.Context().eval(code)
    if _BACKEND == "pythonmonkey" and pm is not None:
        # pythonmonkey 的 pm.eval 共享全局作用域，两次 eval 顶层 const 同名会报
        # "redeclaration of const"。用函数包裹 + 直接 eval，让 const/let 限定在
        # 本次调用的词法环境内，调用结束即销毁，与 quickjs 的新 Context 语义一致。
        return pm.eval(f"(function(){{ return eval({json.dumps(code, ensure_ascii=False)}) }})()")
    raise RuntimeError("未安装 JS 引擎后端：请安装 quickjs 或 pythonmonkey")
