"""作业路由 — 录入/查询/管理作业数据。

录入方式两种：
  1. 逐题录入：POST /homework，questions 数组里每题含 type/difficulty/correct/error_type
  2. 拍照上传：POST /homework/upload，传图片，后端视觉判题自动生成 questions
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from db import repository
from engine.data_models import Homework, QuestionResult

logger = logging.getLogger(__name__)

router = APIRouter()
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class QuestionInput(BaseModel):
    q_num: int
    type: str = "calculation"  # calculation/word_problem/concept/fill_blank/choice
    correct: Optional[bool] = None  # True/False；None = 未作答(空题)
    error_type: Optional[str] = None  # careless/concept/calculation/method/reading
    error_detail: Optional[str] = None
    difficulty: str = "medium"  # easy/medium/hard
    confidence: float = 1.0
    source: str = "parent_confirmed"


class HomeworkInput(BaseModel):
    student_id: str
    date: str  # YYYY-MM-DD
    subject: str = "math"
    grade: int = 0
    total_questions: int
    correct_count: int = 0  # 可省略，以 questions 为准
    wrong_count: int = 0
    blank_count: int = 0
    questions: list[QuestionInput] = []


class HomeworkOutput(BaseModel):
    homework_id: str
    student_id: str
    date: str
    subject: str
    grade: int
    total_questions: int
    correct_count: int
    wrong_count: int
    blank_count: int
    accuracy: float
    completion: float
    created_at: str


def _homework_to_output(hw: Homework, created_at: str) -> HomeworkOutput:
    answered = hw.correct_count + hw.wrong_count
    accuracy = round(hw.correct_count / answered, 4) if answered else 0.0
    completion = round(answered / hw.total_questions, 4) if hw.total_questions else 0.0
    return HomeworkOutput(
        homework_id=hw.homework_id,
        student_id=hw.student_id,
        date=hw.date,
        subject=hw.subject,
        grade=hw.grade,
        total_questions=hw.total_questions,
        correct_count=hw.correct_count,
        wrong_count=hw.wrong_count,
        blank_count=hw.blank_count,
        accuracy=accuracy,
        completion=completion,
        created_at=created_at,
    )


@router.post("/homework", response_model=HomeworkOutput)
async def add_homework(data: HomeworkInput):
    """录入一次作业（逐题）。"""
    # 基本校验
    if not data.questions:
        raise HTTPException(status_code=422, detail="请至少录入一题（questions 不能为空）")
    if data.total_questions < len(data.questions):
        raise HTTPException(
            status_code=422,
            detail=f"total_questions({data.total_questions}) < 题目数({len(data.questions)})",
        )

    # 从题目统计 correct/wrong/blank（以题目为准，覆盖入参的 count）
    correct = sum(1 for q in data.questions if q.correct is True)
    wrong = sum(1 for q in data.questions if q.correct is False)
    blank = sum(1 for q in data.questions if q.correct is None)

    # 错题才能有 error_type
    for q in data.questions:
        if q.error_type and q.correct is not False:
            raise HTTPException(
                status_code=422,
                detail=f"第{q.q_num}题：只有错题才能填 error_type",
            )

    homework_id = f"hw_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    questions = [
        QuestionResult(
            q_num=q.q_num,
            type=q.type,
            correct=q.correct,
            error_type=q.error_type,
            error_detail=q.error_detail,
            difficulty=q.difficulty,
            confidence=q.confidence,
            source=q.source,
        )
        for q in data.questions
    ]
    hw = Homework(
        student_id=data.student_id,
        date=data.date,
        subject=data.subject,
        grade=data.grade,
        homework_id=homework_id,
        total_questions=data.total_questions,
        correct_count=correct,
        wrong_count=wrong,
        blank_count=blank,
        questions=questions,
    )
    repository.save_homework(hw)
    return _homework_to_output(hw, datetime.now().isoformat())


@router.get("/homework/list")
async def list_homework(
    student_id: str = Query(...),
    subject: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """查询作业列表（含逐题统计）。"""
    homeworks = repository.get_homeworks(student_id, subject=subject, limit=limit)
    return {
        "student_id": student_id,
        "subject": subject,
        "total": len(homeworks),
        "homeworks": [_homework_to_output(hw, "").model_dump() for hw in homeworks],
    }


@router.get("/homework/{homework_id}")
async def get_homework(homework_id: str):
    """查单条作业（含完整 questions）。"""
    hw = repository.get_homework(homework_id)
    if not hw:
        raise HTTPException(status_code=404, detail="作业不存在")
    return {
        **_homework_to_output(hw, "").model_dump(),
        "questions": [q.to_dict() for q in hw.questions],
    }


@router.delete("/homework/{homework_id}")
async def delete_homework(homework_id: str):
    """删除一条作业（questions 通过外键级联删除）。"""
    ok = repository.delete_homework(homework_id)
    if not ok:
        raise HTTPException(status_code=404, detail="作业不存在")
    return {"deleted": homework_id, "status": "ok"}


# ---------------------------------------------------------------------------
# 拍照上传（视觉判题全链路）
# ---------------------------------------------------------------------------


@router.post("/homework/upload")
async def upload_homework_image(
    student_id: str = Form(...),
    subject: str = Form("math"),
    grade: int = Form(4),
    date: Optional[str] = Form(None),
    image: UploadFile = File(...),
):
    """拍照上传 → 图转md → 多模型判题 → 存库 → 分析 → 举一反三。

    返回：作业信息 + 报告 + 举一反三练习题。
    """
    # 延迟导入（避免无 API key 时启动就报错）
    from engine.vision_pipeline import VisionPipeline
    from engine.stats_engine import analyze_homework_series
    from engine.bkt_engine import run_bkt_sequence, summarize_bkt
    from engine.rules_engine import run_rules
    from engine.report_generator import ReportGenerator
    from engine.practice_generator import PracticeGenerator

    # 读图片
    raw_bytes = await image.read()
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="图片为空")

    # 压缩图片（手机原图可能 3-5MB，压到 100-300KB 省钱省时）
    from engine.model_client import compress_image
    image_bytes = compress_image(raw_bytes)

    # 1. 视觉判题
    try:
        pipeline = VisionPipeline()
        result = pipeline.process(
            image_bytes=image_bytes,
            student_id=student_id,
            subject=subject,
            grade=grade,
            date=date,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("视觉判题失败")
        raise HTTPException(status_code=500, detail=f"视觉判题失败：{e}")

    hw = result.homework

    # 照片落盘，方便后续回看（前端也可直接用返回的 image_url）
    photo_path = UPLOAD_DIR / f"{hw.homework_id}.jpg"
    try:
        photo_path.write_bytes(image_bytes)
    except Exception as e:
        logger.warning("保存上传照片失败: %s", e)
        photo_path = None

    # 2. 存库
    repository.save_homework(hw)

    # 3. 跑全链路分析（取该学生全部作业，因为趋势/BKT 需要历史）
    homeworks = repository.get_homeworks(student_id, subject=subject)
    if len(homeworks) < 2:
        # 只有这 1 次，也能分析但趋势无意义
        homeworks = [hw]

    results = analyze_homework_series(homeworks, window=5)
    bkt = run_bkt_sequence(homeworks)
    bkt_summary = summarize_bkt(bkt)
    triggered = run_rules(
        series=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        bkt_summary=bkt_summary,
        homework_stats=results["homework_stats"],
        subject=subject,
    )

    weeks = [h.week for h in homeworks if h.week is not None]
    week = max(weeks) if weeks else 1
    dates = sorted(h.date for h in homeworks if h.date)
    date_range = f"{dates[0]} ~ {dates[-1]}" if dates else ""

    report = ReportGenerator(
        student_id=student_id,
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

    # 4. 举一反三
    practices = []
    try:
        gen = PracticeGenerator()
        practices = [p.to_dict() for p in gen.generate(hw)]
    except Exception as e:
        logger.warning("举一反三生成失败（不影响主流程）: %s", e)

    # 5. 持久化报告
    try:
        repository.save_report(
            student_id=student_id,
            subject=subject,
            week=week,
            date_range=date_range,
            report_text=report.format_text(),
            report_dict=report.to_dict(),
        )
    except Exception:
        pass

    # 题目明细：方便家长核对 AI 识别和判题是否准确
    question_details = []
    for q in hw.questions:
        question_details.append({
            "q_num": q.q_num,
            "content": q.question_content or "",
            "student_answer": getattr(q, "student_answer", None) or "",
            "correct": q.correct,
            "error_type": q.error_type,
            "error_detail": q.error_detail,
            "difficulty": q.difficulty,
            "confidence": q.confidence,
        })

    return {
        "homework": _homework_to_output(hw, datetime.now().isoformat()).model_dump(),
        "provider_count": result.provider_count,
        "conflicts": result.conflicts,
        "image_url": f"/api/v1/homework/{hw.homework_id}/image" if photo_path and photo_path.exists() else None,
        "questions": question_details,
        "trend": results["trend"],
        "current_accuracy": results["homework_stats"][-1].accuracy if results["homework_stats"] else 0,
        "mastery": bkt_summary,
        "rules": [r.to_dict() for r in triggered],
        "report_text": report.format_text(),
        "report_sections": [s.to_dict() for s in report.sections],
        "practices": practices,
    }


@router.get("/homework/{homework_id}/image")
async def get_homework_image(homework_id: str):
    """回看某次作业上传的照片。"""
    path = UPLOAD_DIR / f"{homework_id}.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="照片不存在或已被清理")
    return FileResponse(path, media_type="image/jpeg", filename=f"{homework_id}.jpg")
