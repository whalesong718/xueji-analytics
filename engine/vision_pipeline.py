"""学迹分析 · 视觉判题管线。

编排整条链路：
  照片 → [图转md 提取题目] → [多视觉模型并发判题] → [共识融合] → Homework 对象

关键设计：
  - 图转md 只跑一次（用第一个可用 provider），提取题目内容。
  - 判题 并发跑所有 provider，每题取多数票。
  - 融合后的 QuestionResult.source = MODEL_CONSENSUS（原设计预留的枚举值）。
  - 题目内容（md）写入 question_content，供举一反三用。
"""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from engine.data_models import Homework, QuestionResult, AnswerSource, ErrorType
from engine.model_client import (
    ModelClient,
    ProviderConfig,
    QuestionJudgement,
    load_providers,
)

logger = logging.getLogger(__name__)

# 单 provider 超时（秒）
_MODEL_TIMEOUT = 60.0


@dataclass
class PipelineResult:
    """视觉管线输出。"""
    homework: Homework
    extracted_questions: list[dict]  # 图转 md 的原始结果（含题目内容）
    provider_count: int  # 实际参与判题的模型数
    conflicts: list[int]  # 有分歧的题号（供前端提示家长复核）


class VisionPipeline:
    """视觉判题管线。"""

    def __init__(self, providers: Optional[list[ProviderConfig]] = None):
        self.providers = providers or load_providers()
        if not self.providers:
            raise RuntimeError(
                "没有可用的视觉模型 provider。请检查 config/model_providers.yaml "
                "和对应的环境变量 API key。"
            )

    def process(
        self,
        image_bytes: bytes,
        student_id: str,
        subject: str,
        grade: int,
        date: Optional[str] = None,
    ) -> PipelineResult:
        """主入口：照片 → Homework。

        用合并版调用（extract_and_judge），一次调用同时提取题目+判对错，
        省一半时间和费用。多 provider 时并发各自跑一次，再共识融合。
        """
        date = date or datetime.now().strftime("%Y-%m-%d")

        # 1. 所有 provider 并发做「提取+判题」一次调用
        all_judgements = self._extract_and_judge_all(image_bytes)
        provider_count = len(all_judgements)

        if not all_judgements:
            raise ValueError("未能从照片中识别出题目。请确保照片清晰且为作业/试卷内容。")

        logger.info("提取+判题: %d 个模型返回结果", provider_count)

        # 2. 共识融合（每个 provider 的结果已含题目内容+判定）
        merged, conflicts, extracted = self._merge_combined(all_judgements)

        if not merged:
            raise ValueError("未能从照片中识别出题目。请确保照片清晰且为作业/试卷内容。")

        logger.info("融合后: %d 题, %d 处分歧", len(merged), len(conflicts))

        # 3. 组装 Homework
        questions = [self._to_question_result(m) for m in merged]
        homework = self._build_homework(
            questions=questions,
            student_id=student_id,
            subject=subject,
            grade=grade,
            date=date,
        )

        return PipelineResult(
            homework=homework,
            extracted_questions=extracted,
            provider_count=provider_count,
            conflicts=conflicts,
        )

    def _extract_and_judge_all(self, image_bytes: bytes) -> dict[str, list[QuestionJudgement]]:
        """所有 provider 并发做提取+判题。返回 {provider_name: [judgements]}。"""
        results: dict[str, list[QuestionJudgement]] = {}
        with ThreadPoolExecutor(max_workers=len(self.providers)) as pool:
            futures = {
                pool.submit(ModelClient(p, timeout=_MODEL_TIMEOUT).extract_and_judge, image_bytes): p.name
                for p in self.providers
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results[name] = fut.result()
                    logger.info("提取+判题完成: %s (%d 题)", name, len(results[name]))
                except Exception as e:
                    logger.warning("提取+判题失败 %s: %s", name, e)
        return results

    def _merge_combined(
        self,
        judgements_by_provider: dict[str, list[QuestionJudgement]],
    ) -> tuple[list[QuestionJudgement], list[int], list[dict]]:
        """共识融合（合并版）：每个 provider 的结果已含题目内容+判定。

        单 provider 时直接用；多 provider 时按题号对齐投票。
        返回 (merged, conflicts, extracted)。
        """
        all_providers = list(judgements_by_provider.values())
        if not all_providers:
            return [], [], []

        # 单 provider：直接用，无投票
        if len(all_providers) == 1:
            judges = all_providers[0]
            if not judges:
                return [], [], []
            # extracted = 题目内容列表（供 PipelineResult 保留）
            extracted = [
                {"q_num": j.q_num, "content": j.question_content, "student_answer": j.student_answer}
                for j in judges
            ]
            return judges, [], extracted

        # 多 provider：按题号对齐投票
        # 题号集合取并集
        all_qnums = sorted(set(j.q_num for judges in all_providers for j in judges))
        merged: list[QuestionJudgement] = []
        conflicts: list[int] = []
        extracted: list[dict] = []

        for q_num in all_qnums:
            per_provider = []
            for judges in all_providers:
                match = next((j for j in judges if j.q_num == q_num), None)
                if match:
                    per_provider.append(match)

            if not per_provider:
                continue

            # 投票：correct
            correct_votes = Counter(j.correct for j in per_provider if j.correct is not None)
            consensus_correct = correct_votes.most_common(1)[0][0] if correct_votes else None
            if len(correct_votes) > 1:
                top2 = correct_votes.most_common(2)
                if top2[0][1] == top2[1][1]:
                    conflicts.append(q_num)

            # 错误类型投票（仅错题）
            consensus_error_type = None
            consensus_error_detail = None
            if consensus_correct is False:
                et_votes = Counter(j.error_type for j in per_provider if j.error_type and j.correct is False)
                if et_votes:
                    consensus_error_type = et_votes.most_common(1)[0][0]
                consensus_error_detail = next((j.error_detail for j in per_provider if j.error_detail), None)

            # 难度取众数
            diff_votes = Counter(j.difficulty for j in per_provider)
            consensus_diff = diff_votes.most_common(1)[0][0] if diff_votes else "medium"

            # 题目内容取第一个有的
            content = next((j.question_content for j in per_provider if j.question_content), "")
            student_answer = next((j.student_answer for j in per_provider if j.student_answer), None)
            q_type = next((j.question_type for j in per_provider if j.question_type), "calculation")

            # 置信度取平均
            avg_conf = sum(j.confidence for j in per_provider) / len(per_provider)

            merged.append(QuestionJudgement(
                q_num=q_num,
                correct=consensus_correct,
                error_type=consensus_error_type,
                error_detail=consensus_error_detail,
                difficulty=consensus_diff,
                question_content=content,
                student_answer=student_answer,
                confidence=round(avg_conf, 3),
                question_type=q_type,
            ))
            extracted.append({"q_num": q_num, "content": content, "student_answer": student_answer})

        return merged, conflicts, extracted

    def _to_question_result(self, j: QuestionJudgement) -> QuestionResult:
        """把融合后的判定转成引擎能吃的 QuestionResult。"""
        # 校验 error_type 在合法枚举内
        et = j.error_type if j.error_type in {e.value for e in ErrorType} else None
        return QuestionResult(
            q_num=j.q_num,
            type=j.question_type,
            correct=j.correct,
            error_type=et,
            error_detail=j.error_detail,
            difficulty=j.difficulty,
            confidence=j.confidence,
            source=AnswerSource.MODEL_CONSENSUS.value,
            question_content=j.question_content,
        )

    def _build_homework(
        self,
        questions: list[QuestionResult],
        student_id: str,
        subject: str,
        grade: int,
        date: str,
    ) -> Homework:
        """组装 Homework 对象。"""
        correct = sum(1 for q in questions if q.correct is True)
        wrong = sum(1 for q in questions if q.correct is False)
        blank = sum(1 for q in questions if q.correct is None)
        homework_id = f"hw_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        return Homework(
            student_id=student_id,
            date=date,
            subject=subject,
            grade=grade,
            homework_id=homework_id,
            total_questions=len(questions),
            correct_count=correct,
            wrong_count=wrong,
            blank_count=blank,
            questions=questions,
        )
