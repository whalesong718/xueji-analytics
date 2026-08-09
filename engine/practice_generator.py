"""学迹分析 · 举一反三练习生成。

取作业里的错题（含题目原文 question_content），调文本模型生成同类变式题。
家长拿到的是「原题 + 同类练习 + 答案解析」，针对薄弱点巩固。

不依赖视觉能力，用普通文本模型即可（config 里的 practice_model）。
性能：一次请求批量生成所有错题的变式，避免逐题串行拖慢总时长。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional

from engine.data_models import Homework
from engine.model_client import ModelClient, ProviderConfig, load_practice_model, _extract_json

logger = logging.getLogger(__name__)

# 每道错题生成几道变式题
PRACTICE_PER_QUESTION = 2
# 单次最多给几道错题生成练习（控制成本）
MAX_WRONG_QUESTIONS = 3


def humanize_math_text(text: str) -> str:
    """把模型偶发输出的代码化公式，转成家长能直接读的文字。"""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"\$\$([\s\S]*?)\$\$", r"\1", s)
    s = re.sub(r"\$([^$]+)\$", r"\1", s)
    s = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\frac\s*([0-9a-zA-Z]+)\s*([0-9a-zA-Z]+)", r"(\1)/(\2)", s)
    s = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", s)
    s = re.sub(r"\\sqrt\s*([0-9a-zA-Z]+)", r"√\1", s)
    s = s.replace("\\times", "×").replace("\\div", "÷")
    s = s.replace("\\pm", "±").replace("\\approx", "≈")
    s = s.replace("\\leq", "≤").replace("\\geq", "≥").replace("\\neq", "≠").replace("\\cdot", "·")
    s = re.sub(r"\\left|\\right", "", s)
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class PracticeItem:
    """一道举一反三练习题。"""
    original_q_num: int       # 对应的原题号
    error_type: str           # 原题错误类型(中文)
    question: str             # 变式题题目
    answer: str               # 答案
    explanation: str          # 解析
    difficulty: str = "medium"

    def to_dict(self) -> dict:
        return asdict(self)


# 错误类型中文映射
_ERROR_TYPE_CN = {
    "careless": "粗心/习惯",
    "concept": "概念不清",
    "calculation": "计算失误",
    "method": "方法错误",
    "reading": "审题错误",
}


_BATCH_PRACTICE_PROMPT = """你是一位经验丰富的教师。下面有若干道学生错题，请为每道错题各生成{n}道同类变式题。

错题列表：
{wrong_list}

要求：
1. 每道原题生成 {n} 道变式题，考查同一知识点，但变换数字/情境。
2. 难度与原题相当或略低。
3. 题目、答案、解析都必须是中文短句，家长能直接看懂。
4. 数学符号只允许：+ - × ÷ = ≈ < > 1/2 x^2 根号2。
5. 禁止任何代码写法：LaTeX、$...$、\\frac、\\sqrt、\\times、begin、end、pmatrix。
6. 解析控制在 1-2 句，不要长篇公式推导。
7. 只输出 JSON，不要解释。

输出格式：
```json
{{
  "items": [
    {{
      "original_q_num": 2,
      "practices": [
        {{
          "question": "变式题题目",
          "answer": "答案",
          "explanation": "简短解析",
          "difficulty": "easy"
        }}
      ]
    }}
  ]
}}
```
"""


class PracticeGenerator:
    """举一反三练习生成器。"""

    def __init__(self, provider: Optional[ProviderConfig] = None):
        self.provider = provider or load_practice_model()
        if not self.provider:
            raise RuntimeError(
                "没有配置练习题生成模型。请在 config/model_providers.yaml 里配 practice_model。"
            )
        # 批量生成超时稍长一点，但只打 1 次请求
        self.client = ModelClient(self.provider, timeout=45.0)

    def generate(self, homework: Homework) -> list[PracticeItem]:
        """从一次作业的错题生成举一反三练习（批量一次调用）。"""
        wrong = [
            q for q in homework.questions
            if q.correct is False and q.question_content
        ]
        if not wrong:
            return []

        wrong = wrong[:MAX_WRONG_QUESTIONS]
        try:
            return self._generate_batch(wrong)
        except Exception as e:
            logger.warning("举一反三批量生成失败: %s", e)
            return []

    def _generate_batch(self, wrong_questions) -> list[PracticeItem]:
        """一次请求为所有错题生成变式题。"""
        lines = []
        for q in wrong_questions:
            et_cn = _ERROR_TYPE_CN.get(q.error_type, q.error_type or "未知")
            lines.append(
                f"- 原题号: {q.q_num}\n"
                f"  题目: {q.question_content}\n"
                f"  错误类型: {et_cn}\n"
                f"  错误原因: {q.error_detail or '未记录'}\n"
                f"  难度: {q.difficulty}"
            )

        prompt = _BATCH_PRACTICE_PROMPT.format(
            n=PRACTICE_PER_QUESTION,
            wrong_list="\n".join(lines),
        )
        text = self.client.chat_text(prompt)
        parsed = _extract_json(text)
        if not parsed:
            logger.warning("举一反三: 模型返回非 JSON")
            return []

        # 兼容两种结构：items 批量 / practices 单题
        results: list[PracticeItem] = []
        if "items" in parsed and isinstance(parsed["items"], list):
            for item in parsed["items"]:
                q_num = int(item.get("original_q_num", 0))
                et = next(
                    (
                        _ERROR_TYPE_CN.get(q.error_type, q.error_type or "未知")
                        for q in wrong_questions if q.q_num == q_num
                    ),
                    "未知",
                )
                for p in (item.get("practices") or [])[:PRACTICE_PER_QUESTION]:
                    results.append(PracticeItem(
                        original_q_num=q_num,
                        error_type=et,
                        question=humanize_math_text(str(p.get("question", ""))),
                        answer=humanize_math_text(str(p.get("answer", ""))),
                        explanation=humanize_math_text(str(p.get("explanation", ""))),
                        difficulty=str(p.get("difficulty", "medium")),
                    ))
            return results

        # 兜底：如果模型仍返回旧格式 practices，尽量接住
        if "practices" in parsed and isinstance(parsed["practices"], list):
            q0 = wrong_questions[0]
            et = _ERROR_TYPE_CN.get(q0.error_type, q0.error_type or "未知")
            for p in parsed["practices"][:PRACTICE_PER_QUESTION]:
                results.append(PracticeItem(
                    original_q_num=q0.q_num,
                    error_type=et,
                    question=humanize_math_text(str(p.get("question", ""))),
                    answer=humanize_math_text(str(p.get("answer", ""))),
                    explanation=humanize_math_text(str(p.get("explanation", ""))),
                    difficulty=str(p.get("difficulty", "medium")),
                ))
        return results
