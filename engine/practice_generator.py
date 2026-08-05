"""学迹分析 · 举一反三练习生成。

取作业里的错题（含题目原文 question_content），调文本模型生成同类变式题。
家长拿到的是「原题 + 同类练习 + 答案解析」，针对薄弱点巩固。

不依赖视觉能力，用普通文本模型即可（config 里的 practice_model）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional

from engine.data_models import Homework, ErrorType
from engine.model_client import ModelClient, ProviderConfig, load_practice_model, _extract_json

logger = logging.getLogger(__name__)

# 每道错题生成几道变式题
PRACTICE_PER_QUESTION = 3
# 单次最多给几道错题生成练习（控制成本）
MAX_WRONG_QUESTIONS = 5


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


_PRACTICE_PROMPT = """你是一位经验丰富的教师，要根据学生做错的一道题，生成{n}道同类变式题用于巩固练习。

原题信息：
- 题目：{question}
- 学生作答：{student_answer}
- 错误类型：{error_type}
- 错误原因：{error_detail}
- 难度：{difficulty}

要求：
1. 生成 {n} 道变式题，与原题考查同一知识点/同一技能，但变换数字、情境或问法。
2. 难度与原题相当或略低（让学生能做对，建立信心）。
3. 每题附上答案和简短解析。
4. 数学公式用 LaTeX 格式。

输出格式（严格 JSON，不要输出其他内容）：
```json
{{
  "practices": [
    {{
      "question": "变式题题目",
      "answer": "答案",
      "explanation": "简短解析，说明解题要点",
      "difficulty": "easy/medium/hard"
    }}
  ]
}}
```

只输出 JSON。"""


class PracticeGenerator:
    """举一反三练习生成器。"""

    def __init__(self, provider: Optional[ProviderConfig] = None):
        self.provider = provider or load_practice_model()
        if not self.provider:
            raise RuntimeError(
                "没有配置练习题生成模型。请在 config/model_providers.yaml 里配 practice_model。"
            )
        self.client = ModelClient(self.provider, timeout=60.0)

    def generate(self, homework: Homework) -> list[PracticeItem]:
        """从一次作业的错题生成举一反三练习。"""
        # 筛选有题目内容的错题
        wrong = [
            q for q in homework.questions
            if q.correct is False and q.question_content
        ]
        if not wrong:
            return []

        # 限制数量（控制成本）
        wrong = wrong[:MAX_WRONG_QUESTIONS]

        results: list[PracticeItem] = []
        for q in wrong:
            try:
                items = self._generate_for_one(q, homework.subject)
                results.extend(items)
            except Exception as e:
                logger.warning("举一反三生成失败(题%d): %s", q.q_num, e)

        return results

    def _generate_for_one(self, q, subject: str) -> list[PracticeItem]:
        """为单道错题生成变式题。"""
        et_cn = _ERROR_TYPE_CN.get(q.error_type, q.error_type or "未知")
        prompt = _PRACTICE_PROMPT.format(
            n=PRACTICE_PER_QUESTION,
            question=q.question_content,
            student_answer="(未提取)" ,
            error_type=et_cn,
            error_detail=q.error_detail or "未记录",
            difficulty=q.difficulty,
        )

        text = self.client.chat_text(prompt)
        parsed = _extract_json(text)
        if not parsed or "practices" not in parsed:
            logger.warning("举一反三: 模型返回非 JSON, 题%d", q.q_num)
            return []

        items = []
        for p in parsed["practices"][:PRACTICE_PER_QUESTION]:
            items.append(PracticeItem(
                original_q_num=q.q_num,
                error_type=et_cn,
                question=p.get("question", ""),
                answer=p.get("answer", ""),
                explanation=p.get("explanation", ""),
                difficulty=p.get("difficulty", "medium"),
            ))
        return items
