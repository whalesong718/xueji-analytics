"""分析路由 — 全链路分析 + 每周分析 + 掌握度查询。

真实数据版：从 SQLite 读取该学生的作业 → 跑全链路（统计→BKT→规则→报告）→ 返回与
/analyze/demo 同构的 JSON，并自动持久化生成的报告。
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from db import repository
from engine.data_models import Homework
from engine.stats_engine import analyze_homework_series
from engine.bkt_engine import run_bkt_sequence, summarize_bkt
from engine.rules_engine import run_rules
from engine.report_generator import ReportGenerator

router = APIRouter()

# 引擎需要的最少作业数（趋势/BKT 都依赖时序，1 次无意义）
MIN_HOMEWORKS_FOR_ANALYSIS = 2


class AnalyzeRequest(BaseModel):
    student_id: str
    subject: str = "math"
    save_report: bool = True  # 是否持久化报告


def _run_full_pipeline(homeworks: list[Homework], subject: str) -> dict:
    """跑全链路分析，返回与 /analyze/demo 同构的 JSON。"""
    # Phase 1: 统计
    results = analyze_homework_series(homeworks, window=5)

    # Phase 2a: BKT
    bkt = run_bkt_sequence(homeworks)
    bkt_summary = summarize_bkt(bkt)

    # Phase 2b: 规则
    triggered = run_rules(
        series=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        bkt_summary=bkt_summary,
        homework_stats=results["homework_stats"],
        subject=subject,
    )

    # 周次与日期范围
    weeks = [h.week for h in homeworks if h.week is not None]
    week = max(weeks) if weeks else 1
    dates = sorted(h.date for h in homeworks if h.date)
    date_range = f"{dates[0]} ~ {dates[-1]}" if dates else ""

    # Phase 3: 报告
    report = ReportGenerator(
        student_id=homeworks[0].student_id,
        week=week,
        date_range=date_range,
    ).generate(
        series_stats=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        trend=results["trend"],
        homework_stats=results["homework_stats"],
        bkt_summary=bkt_summary,
        rules=triggered,
        subject=subject,
    )

    latest = results["homework_stats"][-1] if results["homework_stats"] else None

    response = {
        "student_id": homeworks[0].student_id,
        "homework_count": results["homework_count"],
        "trend": results["trend"],
        "current_accuracy": latest.accuracy if latest else 0,
        "moving_avg": results["series_stats"].moving_avg,
        "mastery": bkt_summary,
        "rules": [r.to_dict() for r in triggered],
        "report_text": report.format_text(),
        "report_sections": [s.to_dict() for s in report.sections],
        "week": week,
        "date_range": date_range,
    }

    # 持久化报告
    try:
        repository.save_report(
            student_id=homeworks[0].student_id,
            subject=subject,
            week=week,
            date_range=date_range,
            report_text=report.format_text(),
            report_dict=report.to_dict(),
        )
    except Exception:
        # 报告持久化失败不影响返回
        pass

    return response


@router.post("/analyze")
async def full_analysis(data: AnalyzeRequest):
    """全链路分析：从库读真实作业 → 统计 → BKT → 规则 → 报告。"""
    homeworks = repository.get_homeworks(data.student_id, subject=data.subject)
    if len(homeworks) < MIN_HOMEWORKS_FOR_ANALYSIS:
        raise HTTPException(
            status_code=400,
            detail=f"数据不足：至少需要 {MIN_HOMEWORKS_FOR_ANALYSIS} 次作业，当前 {len(homeworks)} 次。请先录入作业。",
        )
    return _run_full_pipeline(homeworks, data.subject)


@router.get("/analyze")
async def analyze_get(
    student_id: str = Query(...),
    subject: str = Query("math"),
):
    """全链路分析（GET 版，方便前端直接调用）。"""
    homeworks = repository.get_homeworks(student_id, subject=subject)
    if len(homeworks) < MIN_HOMEWORKS_FOR_ANALYSIS:
        raise HTTPException(
            status_code=400,
            detail=f"数据不足：至少需要 {MIN_HOMEWORKS_FOR_ANALYSIS} 次作业，当前 {len(homeworks)} 次。请先录入作业。",
        )
    return _run_full_pipeline(homeworks, subject)


@router.get("/analyze/demo")
async def demo_analysis():
    """使用内置模拟数据跑全链路分析（保留，用于无数据时演示）。"""
    import json
    from pathlib import Path

    demo_path = Path(__file__).resolve().parent.parent.parent / "data" / "mock_data.json"
    raw = json.loads(demo_path.read_text(encoding="utf-8"))
    homeworks = [Homework.from_dict(h) for h in raw["homeworks"]]
    meta = raw.get("meta", {})
    subject = meta.get("subject", "math")

    results = analyze_homework_series(homeworks, window=5)
    bkt = run_bkt_sequence(homeworks)
    bkt_summary = summarize_bkt(bkt)
    triggered = run_rules(
        series=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        bkt_summary=bkt_summary,
        homework_stats=results["homework_stats"],
        subject="数学",
    )

    report = ReportGenerator(
        student_id=meta.get("student_id", "student_001"),
        week=4,
        date_range="第13-16天",
    ).generate(
        series_stats=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        trend=results["trend"],
        homework_stats=results["homework_stats"],
        bkt_summary=bkt_summary,
        rules=triggered,
        subject="数学",
    )

    latest = results["homework_stats"][-1] if results["homework_stats"] else None

    return {
        "student_id": meta.get("student_id", "student_001"),
        "homework_count": results["homework_count"],
        "trend": results["trend"],
        "current_accuracy": latest.accuracy if latest else 0,
        "moving_avg": results["series_stats"].moving_avg,
        "mastery": bkt_summary,
        "rules": [r.to_dict() for r in triggered],
        "report_text": report.format_text(),
        "report_sections": [s.to_dict() for s in report.sections],
    }
