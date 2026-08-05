"""作业与报告的 CRUD。

设计要点：
- 读出后组装成与 mock_data.json 同构的 dict，复用 Homework.from_dict（引擎零改动）。
- week 字段写入时留空，读取时按日期归周（以该学生最早作业日为起点，每 7 天一周），
  避免后续插入影响已存的周编号。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from db.database import get_db
from engine.data_models import Homework

# sqlite3.Row 支持 ["col"] 访问，但类型检查器不认，做个别名方便标注
from sqlite3 import Row as sqlite3_row_proxy  # noqa: E402

# 题型/难度/错误类型的合法枚举值（与 engine.data_models 一致）
_VALID_TYPES = {"calculation", "word_problem", "concept", "fill_blank", "choice"}
_VALID_DIFFICULTY = {"easy", "medium", "hard"}
_VALID_ERROR_TYPES = {"careless", "concept", "calculation", "method", "reading"}


# ---------------------------------------------------------------------------
# 作业 CRUD
# ---------------------------------------------------------------------------


def save_homework(hw: Homework) -> str:
    """存一条作业 + 其下所有题目。返回 homework_id。"""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO homeworks
               (homework_id, student_id, date, subject, grade,
                total_questions, correct_count, wrong_count, blank_count,
                week, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hw.homework_id,
                hw.student_id,
                hw.date,
                hw.subject,
                hw.grade,
                hw.total_questions,
                hw.correct_count,
                hw.wrong_count,
                hw.blank_count,
                hw.week,  # 通常为 None，读取时计算
                datetime.now().isoformat(),
            ),
        )
        conn.executemany(
            """INSERT INTO questions
               (homework_id, q_num, type, correct, error_type, error_detail,
                difficulty, confidence, source, question_content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    hw.homework_id,
                    q.q_num,
                    q.type,
                    1 if q.correct is True else (0 if q.correct is False else None),
                    q.error_type,
                    q.error_detail,
                    q.difficulty,
                    q.confidence,
                    q.source,
                    q.question_content,
                )
                for q in hw.questions
            ],
        )
    return hw.homework_id


def _row_to_homework_dict(hw_row: sqlite3_row_proxy, questions: list[dict]) -> dict[str, Any]:
    """把 homeworks 行 + questions 行组装成 Homework.from_dict 能吃的 dict。"""
    return {
        "student_id": hw_row["student_id"],
        "date": hw_row["date"],
        "subject": hw_row["subject"],
        "grade": hw_row["grade"],
        "homework_id": hw_row["homework_id"],
        "total_questions": hw_row["total_questions"],
        "correct_count": hw_row["correct_count"],
        "wrong_count": hw_row["wrong_count"],
        "blank_count": hw_row["blank_count"],
        "questions": questions,
        "week": hw_row["week"],
    }


def _question_row_to_dict(q) -> dict[str, Any]:
    correct = q["correct"]
    return {
        "q_num": q["q_num"],
        "type": q["type"],
        "correct": True if correct == 1 else (False if correct == 0 else None),
        "error_type": q["error_type"],
        "error_detail": q["error_detail"],
        "difficulty": q["difficulty"],
        "confidence": q["confidence"],
        "source": q["source"],
        "question_content": q["question_content"] if "question_content" in q.keys() else None,
    }


def _assign_weeks(homeworks: list[Homework]) -> list[Homework]:
    """按日期归周（以最早作业日为起点，每 7 天一周）。

    仅当作业自身 week 为 None 时计算；已有 week 的保留。
    """
    dated = [h for h in homeworks if h.date]
    if not dated:
        return homeworks
    base = min(datetime.strptime(h.date, "%Y-%m-%d") for h in dated)
    for h in homeworks:
        if h.week is not None:
            continue
        try:
            days = (datetime.strptime(h.date, "%Y-%m-%d") - base).days
            h.week = days // 7 + 1
        except (ValueError, TypeError):
            continue
    return homeworks


def get_homeworks(
    student_id: str,
    subject: Optional[str] = None,
    limit: int = 100,
) -> list[Homework]:
    """查某学生的作业（按日期升序），含逐题数据，并自动归周。"""
    with get_db() as conn:
        if subject:
            hw_rows = conn.execute(
                """SELECT * FROM homeworks
                   WHERE student_id = ? AND subject = ?
                   ORDER BY date ASC, created_at ASC
                   LIMIT ?""",
                (student_id, subject, limit),
            ).fetchall()
        else:
            hw_rows = conn.execute(
                """SELECT * FROM homeworks
                   WHERE student_id = ?
                   ORDER BY date ASC, created_at ASC
                   LIMIT ?""",
                (student_id, limit),
            ).fetchall()

        if not hw_rows:
            return []

        ids = [r["homework_id"] for r in hw_rows]
        q_rows = conn.execute(
            "SELECT * FROM questions WHERE homework_id IN (%s) ORDER BY homework_id, q_num"
            % ",".join("?" * len(ids)),
            ids,
        ).fetchall()

    # 按作业分组题目
    q_by_hw: dict[str, list[dict]] = {}
    for q in q_rows:
        q_by_hw.setdefault(q["homework_id"], []).append(_question_row_to_dict(q))

    homeworks: list[Homework] = []
    for hw_row in hw_rows:
        d = _row_to_homework_dict(hw_row, q_by_hw.get(hw_row["homework_id"], []))
        homeworks.append(Homework.from_dict(d))
    return _assign_weeks(homeworks)


def get_homework(homework_id: str) -> Optional[Homework]:
    """查单条作业。"""
    with get_db() as conn:
        hw_row = conn.execute(
            "SELECT * FROM homeworks WHERE homework_id = ?", (homework_id,)
        ).fetchone()
        if not hw_row:
            return None
        q_rows = conn.execute(
            "SELECT * FROM questions WHERE homework_id = ? ORDER BY q_num",
            (homework_id,),
        ).fetchall()

    d = _row_to_homework_dict(hw_row, [_question_row_to_dict(q) for q in q_rows])
    hw = Homework.from_dict(d)
    # 单条查询也归周（基于该学生全部作业）
    all_hws = get_homeworks(hw_row["student_id"], hw_row["subject"])
    match = next((h for h in all_hws if h.homework_id == homework_id), None)
    return match if match else hw


def delete_homework(homework_id: str) -> bool:
    """删除一条作业（questions 通过外键级联删除）。返回是否删到。"""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM homeworks WHERE homework_id = ?", (homework_id,)
        )
        return cur.rowcount > 0


def count_homeworks(student_id: str, subject: Optional[str] = None) -> int:
    with get_db() as conn:
        if subject:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM homeworks WHERE student_id = ? AND subject = ?",
                (student_id, subject),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM homeworks WHERE student_id = ?",
                (student_id,),
            ).fetchone()
        return int(row["c"])


# ---------------------------------------------------------------------------
# 报告 CRUD
# ---------------------------------------------------------------------------


def save_report(
    student_id: str,
    subject: str,
    week: int,
    date_range: str,
    report_text: str,
    report_dict: dict[str, Any],
) -> str:
    """持久化一份生成的报告。返回 report_id。"""
    report_id = f"rpt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    with get_db() as conn:
        conn.execute(
            """INSERT INTO reports
               (report_id, student_id, subject, week, date_range,
                report_text, report_json, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report_id,
                student_id,
                subject,
                week,
                date_range,
                report_text,
                json.dumps(report_dict, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
    return report_id


def get_latest_report(
    student_id: str, subject: Optional[str] = None
) -> Optional[dict[str, Any]]:
    with get_db() as conn:
        if subject:
            row = conn.execute(
                """SELECT * FROM reports
                   WHERE student_id = ? AND subject = ?
                   ORDER BY generated_at DESC LIMIT 1""",
                (student_id, subject),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM reports
                   WHERE student_id = ?
                   ORDER BY generated_at DESC LIMIT 1""",
                (student_id,),
            ).fetchone()
        return _report_row_to_dict(row) if row else None


def get_report_history(
    student_id: str, subject: Optional[str] = None, limit: int = 10
) -> list[dict[str, Any]]:
    with get_db() as conn:
        if subject:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE student_id = ? AND subject = ?
                   ORDER BY generated_at DESC LIMIT ?""",
                (student_id, subject, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE student_id = ?
                   ORDER BY generated_at DESC LIMIT ?""",
                (student_id, limit),
            ).fetchall()
        return [_report_row_to_dict(r) for r in rows]


def _report_row_to_dict(row) -> dict[str, Any]:
    return {
        "report_id": row["report_id"],
        "student_id": row["student_id"],
        "subject": row["subject"],
        "week": row["week"],
        "date_range": row["date_range"],
        "report_text": row["report_text"],
        "report": json.loads(row["report_json"]) if row["report_json"] else None,
        "generated_at": row["generated_at"],
    }
