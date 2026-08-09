"""本地答案核算器。

对可计算的小学算术题做确定性校验，降低视觉模型误判。
只处理“能稳定解析”的题；解析不了就原样返回，交给模型结果。
"""

from __future__ import annotations

import re
from typing import Optional

from engine.model_client import QuestionJudgement


_NUM = r"-?\d+(?:\.\d+)?"


def _norm_text(s: str) -> str:
    if not s:
        return ""
    t = str(s)
    t = t.replace("×", "*").replace("x", "*").replace("X", "*")
    t = t.replace("÷", "/").replace(":", "/")
    t = t.replace("＝", "=").replace("（", "(").replace("）", ")")
    t = t.replace("．", ".").replace("。", ".")
    t = re.sub(r"\s+", "", t)
    return t


def _to_number(s: str) -> Optional[float]:
    if s is None:
        return None
    t = _norm_text(s)
    # 取最后一个数字，兼容 "答案是12" / "12cm"
    nums = re.findall(_NUM, t)
    if not nums:
        return None
    try:
        return float(nums[-1])
    except ValueError:
        return None


def _almost_equal(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _extract_binary_expr(content: str) -> Optional[tuple[float, str, float]]:
    """从题目中提取 a ? b 形式。"""
    t = _norm_text(content)
    # 优先找 "计算：a?b" / "a?b=?" / "a?b"
    patterns = [
        rf"({_NUM})([+\-*/])({_NUM})=\?",
        rf"({_NUM})([+\-*/])({_NUM})",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            a = float(m.group(1))
            op = m.group(2)
            b = float(m.group(3))
            return a, op, b
    return None


def _eval_binary(a: float, op: str, b: float) -> Optional[float]:
    try:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if abs(b) < 1e-12:
                return None
            return a / b
    except Exception:
        return None
    return None


def local_check_judgement(j: QuestionJudgement) -> QuestionJudgement:
    """对单题做本地核算；无法核算时返回原结果。"""
    content = j.question_content or ""
    answer = j.student_answer or ""

    # 空作答：保持/设为未作答
    if not str(answer).strip():
        j.correct = None
        j.error_type = None
        j.error_detail = None
        j.confidence = max(float(j.confidence or 0), 0.9)
        return j

    expr = _extract_binary_expr(content)
    stu = _to_number(answer)
    if not expr or stu is None:
        return j

    a, op, b = expr
    expected = _eval_binary(a, op, b)
    if expected is None:
        return j

    if _almost_equal(expected, stu):
        j.correct = True
        j.error_type = None
        j.error_detail = None
        j.confidence = max(float(j.confidence or 0), 0.98)
    else:
        j.correct = False
        j.error_type = j.error_type or "calculation"
        j.error_detail = j.error_detail or f"计算结果应为 {expected:g}，学生作答 {stu:g}"
        j.confidence = max(float(j.confidence or 0), 0.95)
    return j


def apply_local_checks(judgements: list[QuestionJudgement]) -> list[QuestionJudgement]:
    return [local_check_judgement(j) for j in judgements]
