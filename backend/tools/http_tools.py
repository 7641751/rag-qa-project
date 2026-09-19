# -*- coding: utf-8 -*-
"""契约格式的错误响应原语：`err()`（返回）/ `api_error()`（抛出）。

契约要求错误体**恒为** `{"code","message"}`（见 docs/api/README.md「错误处理：两段式」），
而 FastAPI 的 `HTTPException` 默认吐的是 `{"detail": ...}`。所以需要这对兄弟函数，
按**调用位置**二选一：

| 函数 | 形态 | 用在 |
|---|---|---|
| `err()` | `return JSONResponse` | 路由函数体内（能直接 return） |
| `api_error()` | `raise HTTPException` | 依赖注入 / service 层（那里只能 raise） |

**为什么必须两套**：依赖注入（`Depends`）和 service 层没有"return 落到响应体"这条路，
只能抛异常；反过来，路由里能 return 时直接返回 `JSONResponse` 比"装全局异常处理器"
更简单直接。两者的 `detail` 形状对齐到同一份契约，P2 会加的统一异常处理器负责把
`detail={"code","message"}` 翻译成最终响应体。

⚠ 本文件是所有层的公共依赖（`upload_function_tools` / `security` / router / service 都会用），
**不要在这里 import 任何项目内的业务模块**，否则 import 环会立刻出现。
"""
from typing import NoReturn

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def err(status: int, code: str, message: str) -> JSONResponse:
    """【路由内 return 用】契约要求错误体是 {"code","message"}，而 raise HTTPException
    会得到 {"detail": ...}，所以流开始前的错误**直接返回 JSONResponse**
    （比装异常处理器简单）。"""
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def api_error(status: int, code: str, message: str) -> NoReturn:
    """【依赖注入 / service 层用】抛契约格式的错误；路由函数内部仍可用 `err()` 直接返回。

    标 `-> NoReturn` 不是装饰，而是**调用方的必需品**。典型写法是这样：

        def hash_password(password: str) -> str:
            raw = password.encode("utf-8")
            if len(raw) > 72:
                api_error(422, "VALIDATION_ERROR", "密码不能超过 72 字节")
            return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")
            #      ↑ 类型检查器不该在这里报 "Missing return statement"

    没有 `NoReturn`，mypy / pyright 会认为上面那条分支有可能不抛异常、函数会走到末尾，
    于是在所有这种"校验失败就抛、否则继续 return"的调用点误报。运行期行为不受影响。
    """
    raise HTTPException(status_code=status, detail={"code": code, "message": message})
