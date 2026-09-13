# -*- coding: utf-8 -*-
"""实测：APIRouter(prefix="api/kb") 缺前导斜杠会不会导致路由匹配不上。"""
from fastapi import APIRouter, FastAPI


def build(prefix: str) -> FastAPI:
    r = APIRouter(prefix=prefix, tags=["kb"])

    @r.get("/documents")
    async def _list():
        return {"ok": True}

    a = FastAPI()
    a.include_router(r)
    return a


for p in ("api/kb", "/api/kb"):
    try:
        app = build(p)
    except Exception as exc:
        print(f"prefix={p!r:12} 构造即失败 -> {type(exc).__name__}: {exc}")
        continue
    paths = list(app.openapi()["paths"])
    try:
        from fastapi.testclient import TestClient
        status = TestClient(app).get("/api/kb/documents").status_code
    except Exception as exc:                       # httpx 缺失时退化：只看 openapi 路径
        status = f"TestClient 不可用({type(exc).__name__})"
    print(f"prefix={p!r:12} openapi_paths={paths} GET /api/kb/documents -> {status}")
