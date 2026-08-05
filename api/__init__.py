"""学迹分析 · API 入口

启动：
  cd /mnt/e/xueji-analytics
  pip install fastapi uvicorn
  python -m api.main

或：
  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import analysis, report, homework

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


@app.get("/")
async def root():
    return {"service": "学迹分析", "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}