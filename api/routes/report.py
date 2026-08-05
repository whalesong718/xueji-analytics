"""报告路由 — 获取历史报告 / 周报对比。

真实数据版：从 SQLite reports 表读取。
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from db import repository

router = APIRouter()


@router.get("/report/latest")
async def latest_report(
    student_id: str = Query(...),
    subject: Optional[str] = Query(None),
):
    """获取最新周报。"""
    report = repository.get_latest_report(student_id, subject=subject)
    if not report:
        raise HTTPException(
            status_code=404,
            detail="暂无报告。请先录入作业并调用 /analyze 生成报告。",
        )
    return report


@router.get("/report/history")
async def report_history(
    student_id: str = Query(...),
    subject: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
):
    """历史报告列表。"""
    reports = repository.get_report_history(student_id, subject=subject, limit=limit)
    return {
        "student_id": student_id,
        "subject": subject,
        "total": len(reports),
        "reports": reports,
    }


@router.get("/report/compare")
async def compare_reports(
    student_id: str = Query(...),
    week_from: int = Query(...),
    week_to: int = Query(...),
    subject: Optional[str] = Query(None),
):
    """对比两周报告。返回两份报告 + 关键指标变化。"""
    reports = repository.get_report_history(student_id, subject=subject, limit=100)

    def find_by_week(w):
        for r in reports:
            if r.get("week") == w:
                return r
        return None

    r_from = find_by_week(week_from)
    r_to = find_by_week(week_to)
    if not r_from:
        raise HTTPException(status_code=404, detail=f"第{week_from}周报告不存在")
    if not r_to:
        raise HTTPException(status_code=404, detail=f"第{week_to}周报告不存在")

    # 提取关键指标做对比（metrics 用中文键，值为字符串如 "88.9%"）
    def metrics_of(r):
        rep = r.get("report") or {}
        sections = rep.get("sections", [])
        overview = next((s for s in sections if "概览" in s.get("title", "")), {})
        m = overview.get("metrics", {})
        return {
            "week": r.get("week"),
            "accuracy": m.get("当前正确率"),
            "moving_avg": m.get("移动平均(5次)"),
            "trend": m.get("趋势"),
        }

    return {
        "student_id": student_id,
        "week_from": week_from,
        "week_to": week_to,
        "from": metrics_of(r_from),
        "to": metrics_of(r_to),
        "report_from": r_from,
        "report_to": r_to,
    }
