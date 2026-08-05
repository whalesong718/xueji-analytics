"""
学业量化分析引擎 — 规则引擎层。

规则定义：条件 → 结论（带置信度 + 触发依据）
规则按重要性排序，单条触发后就输出，避免重复。

规则类型：
  R001: 持续进步     — 正确率连续上升
  R002: 反复犯错     — 某类错误重复出现
  R003: 问题已解决   — 上周有本周无
  R004: 新出现问题   — 本周首次出现
  R005: 难题放弃     — 难题空题多
  R006: 进步停滞     — 多周未改善
  R007: 计算改善     — 计算失误连续减少
  R008: 退步预警     — 正确率连续下降
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Optional

from engine.data_models import (
    ErrorType,
    ErrorPatternStats,
    HomeworkStats,
    SeriesStats,
    TrackingStatus,
)


@dataclass
class RuleOutput:
    """单条规则触发的输出。"""
    rule_id: str
    rule_name: str
    template: str
    confidence: str  # high / medium / low
    trigger_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "message": self.format_message(),
            "confidence": self.confidence,
            "trigger_data": self.trigger_data,
        }

    def format_message(self) -> str:
        """用 trigger_data 填充模板。"""
        try:
            return self.template.format(**self.trigger_data)
        except KeyError:
            return self.template


# ---------------------------------------------------------------------------
# 规则函数
# ---------------------------------------------------------------------------

# 每条规则函数签名：
#   func(series, error_patterns, error_tracking, bkt_summary) -> Optional[RuleOutput]


def check_rule_R001(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
    subject: str = "数学",
) -> Optional[RuleOutput]:
    """R001: 持续进步 — 趋势斜率 > 0 且连续改善 >= 2。"""
    if series.progress_slope is not None and series.progress_slope > 0.01:
        if series.consecutive_improving >= 2:
            # 找本周比上周减少最多的错误类型
            best_improvement = None
            best_delta = 0
            for p in error_patterns:
                delta = p.last_week_count - p.this_week_count
                if delta > best_delta:
                    best_delta = delta
                    best_improvement = p

            detail = ""
            if best_improvement and best_delta > 0:
                detail = f"，尤其是{_error_type_cn(best_improvement.error_type)}从{best_improvement.last_week_count}次减到{best_improvement.this_week_count}次"
            else:
                detail = "，继续保持"

            # 最近5次移动平均
            mov = f"{series.moving_avg:.0%}" if series.moving_avg is not None else "良好"

            return RuleOutput(
                rule_id="R001",
                rule_name="持续进步",
                template="{subject}正确率连续{N}周上升{detail}",
                confidence="high",
                trigger_data={
                    "subject": subject,
                    "N": series.consecutive_improving,
                    "detail": detail,
                    "moving_avg": mov,
                },
            )
    return None


def check_rule_R002(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
) -> Optional[RuleOutput]:
    """R002: 反复犯错 — 重复率 >= 0.6 且出现次数 >= 3。"""
    for p in error_patterns:
        if p.repeat_rate >= 0.6 and p.occurrence_count >= 3:
            return RuleOutput(
                rule_id="R002",
                rule_name="反复犯错",
                template="反复出现的问题：{error_type}，近{N}次中出现{M}次",
                confidence="high",
                trigger_data={
                    "error_type": _error_type_cn(p.error_type),
                    "N": 5,
                    "M": p.occurrence_count,
                    "repeat_rate": f"{p.repeat_rate:.1%}",
                },
            )
    return None


def check_rule_R003(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
) -> Optional[RuleOutput]:
    """R003: 问题已解决 — 上周>0 本周=0 且 BKT 掌握度>0.8。"""
    for p in error_patterns:
        if p.last_week_count > 0 and p.this_week_count == 0:
            mastery = bkt_summary.get(p.error_type, {}).get("mastery", 0)
            if mastery > 0.6:  # 更宽松的阈值
                return RuleOutput(
                    rule_id="R003",
                    rule_name="问题已解决",
                    template="已解决：{error_type}，上周{M}次，本周0次",
                    confidence="high",
                    trigger_data={
                        "error_type": _error_type_cn(p.error_type),
                        "M": p.last_week_count,
                    },
                )
    return None


def check_rule_R004(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
) -> Optional[RuleOutput]:
    """R004: 新出现问题 — 本周>0 且 之前从未出现。

    检查全部历史数据（不只是上周窗口）。
    如果只有本周的数据（counts_per_homework 长度 <= 5），无法判断是否新问题，跳过。
    """
    for p in error_patterns:
        if p.this_week_count > 0 and p.last_week_count == 0:
            n = len(p.counts_per_homework)
            # 如果只有本周数据（<=5次），无法判断是否新问题
            if n <= 5:
                continue
            # 检查本周之前的所有数据
            prior = p.counts_per_homework[:-5]
            if sum(prior) > 0:
                continue  # 之前出现过，不是新问题
            return RuleOutput(
                rule_id="R004",
                rule_name="新出现问题",
                template="新出现的问题：{error_type}，本周首次出现{M}次",
                confidence="medium",
                trigger_data={
                    "error_type": _error_type_cn(p.error_type),
                    "M": p.this_week_count,
                },
            )
    return None


def check_rule_R005(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
    homework_stats: list[HomeworkStats] = None,
) -> Optional[RuleOutput]:
    """R005: 难题放弃 — 难题空题 >= 2 且连续出现。"""
    if homework_stats is None:
        return None

    recent = homework_stats[-5:] if len(homework_stats) >= 5 else homework_stats
    blank_hard_sessions = [s for s in recent if s.blank_hard_count >= 2]

    if len(blank_hard_sessions) >= 2:
        # 检查是否连续
        if blank_hard_sessions[-1].blank_hard_count >= 2:
            # 倒数第2个也是？或者看总次数
            return RuleOutput(
                rule_id="R005",
                rule_name="难题放弃",
                template="难题放弃倾向：近期{N}次作业中{M}次最后一道大题未作答",
                confidence="high",
                trigger_data={
                    "N": len(recent),
                    "M": len(blank_hard_sessions),
                },
            )
    return None


def check_rule_R006(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
) -> Optional[RuleOutput]:
    """R006: 进步停滞 — 某类错误 BKT 掌握度<0.6 且多周无改善。"""
    for e in ErrorType:
        info = bkt_summary.get(e.value, {})
        mastery = info.get("mastery", 0.5)
        if mastery < 0.5 and info.get("observations", 0) >= 5:
            return RuleOutput(
                rule_id="R006",
                rule_name="进步停滞",
                template="{error_type}持续未改善，观察{obs}次，掌握度{mastery}",
                confidence="medium",
                trigger_data={
                    "error_type": _error_type_cn(e.value),
                    "obs": info.get("observations", 0),
                    "mastery": f"{mastery:.0%}",
                },
            )
    return None


def check_rule_R007(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
) -> Optional[RuleOutput]:
    """R007: 计算失误改善 — 计算类错误连续减少。"""
    calc = next((p for p in error_patterns if p.error_type == ErrorType.CALCULATION.value), None)
    if calc and calc.this_week_count < calc.last_week_count:
        # 看连续减少的次数
        decreasing = 0
        for i in range(len(calc.counts_per_homework) - 1, 0, -1):
            if calc.counts_per_homework[i] < calc.counts_per_homework[i - 1]:
                decreasing += 1
            else:
                break
        if decreasing >= 2:
            return RuleOutput(
                rule_id="R007",
                rule_name="计算失误改善",
                template="计算失误连续{N}周减少，从每周{old}次降到{new}次",
                confidence="high",
                trigger_data={
                    "N": decreasing,
                    "old": calc.last_week_count,
                    "new": calc.this_week_count,
                },
            )
    return None


def check_rule_R008(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
    subject: str = "数学",
) -> Optional[RuleOutput]:
    """R008: 退步预警 — 斜率 < -0.02 且连续下降 >= 2。"""
    if series.progress_slope is not None and series.progress_slope < -0.02:
        if series.consecutive_declining >= 2:
            accs = series.accuracies
            n = len(accs)
            # 退步开始前的正确率 vs 当前正确率
            decline_start_idx = n - series.consecutive_declining - 1
            if decline_start_idx >= 0:
                old = accs[decline_start_idx] * 100
                new = accs[-1] * 100
            else:
                old = accs[0] * 100
                new = accs[-1] * 100

            return RuleOutput(
                rule_id="R008",
                rule_name="退步预警",
                template="{subject}正确率连续{N}次下降，从{old:.0f}%降到{new:.0f}%",
                confidence="high",
                trigger_data={
                    "subject": subject,
                    "N": series.consecutive_declining,
                    "old": old,
                    "new": new,
                },
            )
    return None


# ---------------------------------------------------------------------------
# 规则注册表
# ---------------------------------------------------------------------------

RULES = [
    # 优先级排序：正面 > 负面 > 中性
    ("R001", check_rule_R001),     # 持续进步
    ("R007", check_rule_R007),     # 计算改善
    ("R003", check_rule_R003),     # 问题已解决
    ("R002", check_rule_R002),     # 反复犯错
    ("R004", check_rule_R004),     # 新出现问题
    ("R005", check_rule_R005),     # 难题放弃
    ("R006", check_rule_R006),     # 进步停滞
    ("R008", check_rule_R008),     # 退步预警
]


# ---------------------------------------------------------------------------
# 规则引擎入口
# ---------------------------------------------------------------------------


def run_rules(
    series: SeriesStats,
    error_patterns: list[ErrorPatternStats],
    error_tracking: dict[str, str],
    bkt_summary: dict,
    homework_stats: list[HomeworkStats] = None,
    subject: str = "数学",
) -> list[RuleOutput]:
    """运行所有规则，返回触发的规则输出列表。

    Returns:
        按优先级排序的触发规则列表。
    """
    triggered = []
    kwargs = {
        "series": series,
        "error_patterns": error_patterns,
        "error_tracking": error_tracking,
        "bkt_summary": bkt_summary,
        "homework_stats": homework_stats,
        "subject": subject,
    }
    for rule_id, rule_fn in RULES:
        # 只传规则函数接受的参数
        sig = inspect.signature(rule_fn)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        result = rule_fn(**filtered)
        if result is not None:
            triggered.append(result)
    return triggered


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _error_type_cn(error_type: str) -> str:
    """错误类型中文名。"""
    mapping = {
        "careless": "粗心/习惯",
        "concept": "概念不清",
        "calculation": "计算失误",
        "method": "方法错误",
        "reading": "审题错误",
    }
    return mapping.get(error_type, error_type)


def format_rule_summary(triggered: list[RuleOutput]) -> list[dict]:
    """格式化规则输出为可读列表。"""
    return [r.to_dict() for r in triggered]