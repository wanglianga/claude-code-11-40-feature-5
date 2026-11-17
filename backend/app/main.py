from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import API_PREFIX
from .db import init_db
from .routers import auth, catalog, probability, records, spatial, surveys


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="自然保护区季节调查完整性与覆盖数据服务",
    description=(
        "交付季节调查覆盖与有效性证书数据：调查单元、规则证书、概率估计与趋势引用。"
        "服务不派发巡护任务、不预约人员器材，也不把缺口转成行动指令。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, catalog, surveys, records, probability, spatial):
    app.include_router(module.router, prefix=API_PREFIX)
    public = getattr(module, "public_router", None)
    if public is not None:
        app.include_router(public, prefix=API_PREFIX)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "seasonal-survey-integrity"}
