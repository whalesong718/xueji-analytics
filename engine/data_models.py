"""学业量化分析引擎 — 数据结构定义。

对应引擎方案：单次作业输入 / 周报输出 / 错误类型枚举。
纯标准库，无第三方依赖。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class Subject(str, Enum):
    MATH = "math"
    CHINESE = "chinese"
    ENGLISH = "english"


class QuestionType(str, Enum):
    CALCULATION = "calculation"
    WORD_PROBLEM = "word_problem"
    CONCEPT = "concept"
    FILL_BLANK = "fill_blank"
    CHOICE = "choice"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ErrorType(str, Enum):
    """五类错误（引擎按错误类型做 BKT，不按知识点）。"""

    CARELESS = "careless"  # 粗心/习惯
    CONCEPT = "concept"  # 概念不清
    CALCULATION = "calculation"  # 计算失误
    METHOD = "method"  # 方法错误
    READING = "reading"  # 审题错误


class AnswerSource(str, Enum):
    MODEL_CONSENSUS = "model_consensus"
    PARENT_CONFIRMED = "parent_confirmed"
    OCR_BLANK = "ocr_blank"
    MOCK = "mock"


class TrackingStatus(str, Enum):
    IMPROVING = "improving"
    RESOLVED = "resolved"
    NO_CHANGE = "no_change"
    WORSENING = "worsening"
    NEW = "new"


# ---------------------------------------------------------------------------
# 输入：单次作业
# ---------------------------------------------------------------------------


@dataclass
class QuestionResult:
    q_num: int
    type: str  # QuestionType value
    correct: Optional[bool]  # True/False；None = 未作答
    error_type: Optional[str] = None  # ErrorType value or None
    error_detail: Optional[str] = None
    difficulty: str = Difficulty.MEDIUM.value
    confidence: float = 1.0
    source: str = AnswerSource.MOCK.value
    question_content: Optional[str] = None  # 题目原文，视觉判题才有，举一反三用
    student_answer: Optional[str] = None  # 学生作答原文，视觉判题才有

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuestionResult":
        return cls(
            q_num=int(data["q_num"]),
            type=str(data["type"]),
            correct=data.get("correct"),
            error_type=data.get("error_type"),
            error_detail=data.get("error_detail"),
            difficulty=str(data.get("difficulty", Difficulty.MEDIUM.value)),
            confidence=float(data.get("confidence", 1.0)),
            source=str(data.get("source", AnswerSource.MOCK.value)),
            question_content=data.get("question_content"),
            student_answer=data.get("student_answer"),
        )


@dataclass
class HomeworkSummary:
    accuracy: float
    completion: float
    hard_correct_rate: float
    error_type_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HomeworkSummary":
        return cls(
            accuracy=float(data["accuracy"]),
            completion=float(data["completion"]),
            hard_correct_rate=float(data.get("hard_correct_rate", 0.0)),
            error_type_distribution=dict(data.get("error_type_distribution", {})),
        )


@dataclass
class Homework:
    """单次作业数据（分析引擎输入）。"""

    student_id: str
    date: str  # YYYY-MM-DD
    subject: str
    grade: int
    homework_id: str
    total_questions: int
    correct_count: int
    wrong_count: int
    blank_count: int
    questions: list[QuestionResult] = field(default_factory=list)
    summary: Optional[HomeworkSummary] = None
    week: Optional[int] = None  # 模拟数据用：第几周

    def to_dict(self) -> dict[str, Any]:
        d = {
            "student_id": self.student_id,
            "date": self.date,
            "subject": self.subject,
            "grade": self.grade,
            "homework_id": self.homework_id,
            "total_questions": self.total_questions,
            "correct_count": self.correct_count,
            "wrong_count": self.wrong_count,
            "blank_count": self.blank_count,
            "questions": [q.to_dict() for q in self.questions],
        }
        if self.summary is not None:
            d["summary"] = self.summary.to_dict()
        if self.week is not None:
            d["week"] = self.week
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Homework":
        questions = [QuestionResult.from_dict(q) for q in data.get("questions", [])]
        summary = None
        if "summary" in data and data["summary"] is not None:
            summary = HomeworkSummary.from_dict(data["summary"])
        return cls(
            student_id=str(data["student_id"]),
            date=str(data["date"]),
            subject=str(data["subject"]),
            grade=int(data["grade"]),
            homework_id=str(data["homework_id"]),
            total_questions=int(data["total_questions"]),
            correct_count=int(data["correct_count"]),
            wrong_count=int(data["wrong_count"]),
            blank_count=int(data["blank_count"]),
            questions=questions,
            summary=summary,
            week=data.get("week"),
        )


# ---------------------------------------------------------------------------
# 输出：周报（结构先定义，Phase 3 填充）
# ---------------------------------------------------------------------------


@dataclass
class RecurringProblem:
    problem: str
    frequency: str
    trend: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrackingItem:
    problem: str
    last_week: int
    this_week: int
    status: str  # TrackingStatus value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SuggestionRow:
    problem: str
    severity: str
    suggestion: str
    frequency: str
    expected: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanItem:
    day: str
    subject: str
    task: str
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WeeklyReport:
    """周报数据（分析引擎输出）。"""

    student_id: str
    week: int
    date_range: str
    homework_count: int
    sections: dict[str, Any] = field(default_factory=dict)
    feedback: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": True,
            "options": ["accurate", "emotional", "disagree", "supplement"],
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "week": self.week,
            "date_range": self.date_range,
            "homework_count": self.homework_count,
            "sections": self.sections,
            "feedback": self.feedback,
        }


# ---------------------------------------------------------------------------
# 统计层中间结果
# ---------------------------------------------------------------------------


@dataclass
class HomeworkStats:
    """单次作业统计结果。"""

    homework_id: str
    date: str
    accuracy: float  # correct / (correct + wrong)，空题不计入分母
    completion: float  # (correct + wrong) / total
    hard_correct_rate: float
    error_type_counts: dict[str, int] = field(default_factory=dict)
    error_type_share: dict[str, float] = field(default_factory=dict)
    blank_hard_count: int = 0
    total_questions: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    blank_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SeriesStats:
    """时间序列统计（移动平均、斜率、稳定性等）。"""

    window: int
    accuracies: list[float]
    moving_avg: Optional[float]
    ewma: Optional[float]
    progress_slope: Optional[float]
    stability_variance: Optional[float]
    consecutive_improving: int = 0
    consecutive_declining: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ErrorPatternStats:
    """错误模式统计。"""

    error_type: str
    counts_per_homework: list[int]
    repeat_rate: float  # 近 window 次出现次数 / window
    occurrence_count: int  # 近 window 次中出现过的次数
    last_week_count: int
    this_week_count: int
    self_correction_rate: Optional[float] = None  # 自纠率

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_summary_from_questions(questions: list[QuestionResult]) -> HomeworkSummary:
    """从题目列表推导 summary（生成模拟数据 / 校验用）。"""
    total = len(questions)
    correct = sum(1 for q in questions if q.correct is True)
    wrong = sum(1 for q in questions if q.correct is False)
    blank = sum(1 for q in questions if q.correct is None)
    answered = correct + wrong
    accuracy = (correct / answered) if answered else 0.0
    completion = (answered / total) if total else 0.0

    hard = [q for q in questions if q.difficulty == Difficulty.HARD.value]
    hard_answered = [q for q in hard if q.correct is not None]
    hard_correct = sum(1 for q in hard_answered if q.correct is True)
    hard_rate = (hard_correct / len(hard_answered)) if hard_answered else 0.0

    dist = {e.value: 0 for e in ErrorType}
    for q in questions:
        if q.correct is False and q.error_type:
            if q.error_type in dist:
                dist[q.error_type] += 1
            else:
                dist[q.error_type] = dist.get(q.error_type, 0) + 1

    return HomeworkSummary(
        accuracy=round(accuracy, 4),
        completion=round(completion, 4),
        hard_correct_rate=round(hard_rate, 4),
        error_type_distribution=dist,
    )
