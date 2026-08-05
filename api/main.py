"""学迹分析 · API 入口

启动：
  uvicorn api.main:app --host 0.0.0.0 --port 8000

部署到 Render：git push → render.yaml 自动构建
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from api.routes import analysis, report, homework
from db.database import init_db

app = FastAPI(
    title="学迹分析 API",
    description="学业量化分析引擎后端。接收作业数据 → 返回分析报告。",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router, prefix="/api/v1", tags=["分析"])
app.include_router(report.router, prefix="/api/v1", tags=["报告"])
app.include_router(homework.router, prefix="/api/v1", tags=["作业"])


@app.on_event("startup")
def _startup():
    """启动时建表（幂等）。"""
    init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}


# 挂载前端静态文件 —— 必须放在所有显式路由之后，
# 否则通配 /* 会拦截 /health 等路由并返回 404
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")