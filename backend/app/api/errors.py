# backend/app/api/errors.py
"""把异常翻译成契约错误体 {"code","message"}。

形状的唯一真相源是 backend/tools/http_tools.py（err() 直接返回 /
api_error() 抛 HTTPException(detail={"code","message"})）。本文件只负责
把异常接到 FastAPI 上：改形状 → 先改 http_tools，再改这里的翻译。
"""
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Starlette 内置异常只带一句英文短语，直接当 message 会得到
# status=404 + code=INTERNAL_ERROR 的矛盾组合；按状态码补常见映射。
_STATUS_CODE_MAP = {
    401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED", 422: "VALIDATION_ERROR", 500: "INTERNAL_ERROR",
}


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """detail 已是契约形状 → 原样；否则按状态码补 code。"""
    d = exc.detail
    body = dict(d) if isinstance(d, Mapping) and "code" in d else {
        "code": _STATUS_CODE_MAP.get(exc.status_code, "INTERNAL_ERROR"),
        "message": str(d),
    }
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """修掉既有契约违背：422 原先返回 FastAPI 默认的 {"detail": [...]}。"""
    first = (exc.errors() or [{}])[0]
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = first.get("msg", "校验失败")
    return JSONResponse(status_code=422, content={
        "code": "VALIDATION_ERROR", "message": f"{loc}: {msg}" if loc else msg})


def register_error_handlers(app: FastAPI) -> None:
    """在 main.py 里紧跟 `app = FastAPI(...)` 调一次。"""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
