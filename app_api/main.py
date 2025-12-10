# -*- coding: utf-8 -*-
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app_api.core.logging import logger
from app_api.api.routes import router as api_router


app = FastAPI(title="Story2Video Model Service", version="1.0.0")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"HTTP {request.method} {request.url}")
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        logger.error(f"请求处理中异常: {e}")
        raise


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"全局异常: {exc}")
    return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": str(exc)})


app.include_router(api_router)

try:
    from app_api.core.config import OUTPUT_DIR
    app.mount("/static", StaticFiles(directory=str(OUTPUT_DIR), html=False), name="static")
except Exception as e:
    logger.error(f"静态目录挂载失败: {e}")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
    except Exception:
        body = b""
    import json
    logger.error(f"请求 422 验证错误:\n错误详情: {json.dumps(exc.errors(), indent=2, ensure_ascii=False)}\n请求体: {body.decode(errors='ignore')}")
    return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "errors": exc.errors()})


if __name__ == "__main__":
    from app_api.core.config import SERVICE_PORT
    uvicorn.run("app.main:app", host="0.0.0.0", port=SERVICE_PORT, reload=True)
