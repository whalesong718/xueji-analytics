"""
学业量化分析引擎 — 报告生成层。

输入：Phase 1（统计）+ Phase 2（BKT + 规则）的输出
输出：WeeklyReport 对象 + 可读的文本摘要

报告结构：
  1. 概览卡片 — 正确率、完成率、趋势、掌握度
  2. 错误分析 — 本周 vs 上周对比，重点关注
  3. 规则结论 — 触发的规则及置信度
  4. 学习建议 — 基于错误类型和掌握度的行动建议
  5. 下周计划 — 建议的练习重点
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from engine.data_models import (
    ErrorType,
    ErrorPatternStats,
    HomeworkStats,
    SeriesStats,
    TrackingStatus,
    WeeklyReport,
    RecurringProblem,
    TrackingItem,
    SuggestionRow,
    PlanItem,
)
from engine.rules_engine import RuleOutput


# ---------------------------------------------------------------------------
# 报告截面：某一周/某段时间的完整报告数据
# ---------------------------------------------------------------------------


@dataclass
class ReportSection:
    """报告中的一个章节。"""

    title: str
    summary: str
    items: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    level: str = "info"  # info / warning / success / danger

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StudentReport:
    """完整学生报告。"""

    student_id: str
    week: int
    date_range: str
    generated_at: str
    sections: list[ReportSection] = field(default_factory=list)
    raw_weekly_report: Optional[WeeklyReport] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "week": self.week,
            "date_range": self.date_range,
            "generated_at": self.generated_at,
            "sections": [asdict(s) for s in self.sections],
        }

    def format_text(self) -> str:
        """输出人类可读的纯文本报告。"""
        lines = []
        sep = "=" * 60
        lines.append(sep)
        lines.append(f"  学业分析报告 — {self.student_id}")
        lines.append(f"  第{self.week}周（{self.date_range}）")
        lines.append(sep)
        lines.append("")

        for section in self.sections:
            # 图标
            icon_map = {
                "success": "✅",
                "warning": "⚠️",
                "danger": "🔴",
                "info": "📋",
            }
            icon = icon_map.get(section.level, "📋")
            lines.append(f"{icon} {section.title}")
            lines.append("-" * 40)
            lines.append(section.summary)
            lines.append("")

            if section.metrics:
                for k, v in section.metrics.items():
                    lines.append(f"  {k}: {v}")
                lines.append("")

            if section.items:
                for item in section.items:
                    if "problem" in item and "suggestion" in item:
                        lines.append(f"  • {item['problem']}")
                        lines.append(f"    建议: {item['suggestion']}")
                    elif "rule_id" in item and "message" in item:
                        icon_conf = {
                            "high": "🟢",
                            "medium": "🟡",
                            "low": "⚪",
                        }
                        ci = icon_conf.get(item.get("confidence", ""), "⚪")
                        lines.append(f"  {ci} [{item['rule_id']}] {item['message']}")
                    else:
                        for k, v in item.items():
                            lines.append(f"  {k}: {v}")
                lines.append("")

        lines.append(sep)
        lines.append("  报告生成完毕 ✅")
        lines.append(sep)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 报告生成器
# ---------------------------------------------------------------------------


class ReportGenerator:
    """将各层分析结果整合为结构化报告。"""

    def __init__(
        self,
        student_id: str,
        week: int,
        date_range: str,
    ):
        self.student_id = student_id
        self.week = week
        self.date_range = date_range

    def generate(
        self,
        *,
        # 统计层
        series_stats: SeriesStats,
        error_patterns: list[ErrorPatternStats],
        error_tracking: dict[str, str],
        trend: str,
        homework_stats: list[HomeworkStats],
        # BKT层
        bkt_summary: dict[str, dict],
        # 规则层
        rules: list[RuleOutput],
        # 上下文
        subject: str = "数学",
    ) -> StudentReport:
        """生成完整报告。"""
        sections: list[ReportSection] = []

        # ---- Section 1: 概览 ----
        sections.append(self._build_overview(
            series_stats, trend, bkt_summary, homework_stats, subject
        ))

        # ---- Section 2: BKT掌握度分析 ----
        sections.append(self._build_mastery(
            bkt_summary, error_tracking
        ))

        # ---- Section 3: 错误分析 ----
        sections.append(self._build_errors(
            error_patterns, error_tracking, bkt_summary
        ))

        # ---- Section 4: 规则结论 ----
        sections.append(self._build_rules(
            rules, series_stats, error_patterns
        ))

        # ---- Section 5: 学习建议 ----
        sections.append(self._build_suggestions(
            error_patterns, bkt_summary, rules, error_tracking, subject
        ))

        # ---- Section 6: 下周计划 ----
        sections.append(self._build_plan(
            error_patterns, bkt_summary, error_tracking, subject
        ))

        report = StudentReport(
            student_id=self.student_id,
            week=self.week,
            date_range=self.date_range,
            generated_at="",  # 调用方填时间
            sections=sections,
        )

        # 同时构建 WeeklyReport（与 data_models 兼容）
        weekly = self._build_weekly_report(
            sections, series_stats, error_patterns, homework_stats
        )
        report.raw_weekly_report = weekly

        return report

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_overview(
        self,
        series: SeriesStats,
        trend: str,
        bkt_summary: dict[str, dict],
        homework_stats: list[HomeworkStats],
        subject: str,
    ) -> ReportSection:
        n = len(homework_stats)
        latest = homework_stats[-1] if homework_stats else None

        metrics = {
            "作业次数": str(n),
            "当前正确率": f"{latest.accuracy:.1%}" if latest else "N/A",
            "移动平均(5次)": f"{series.moving_avg:.1%}" if series.moving_avg is not None else "N/A",
            "趋势": _trend_cn(trend),
        }

        # 掌握度概览
        mastered = sum(1 for v in bkt_summary.values() if v["mastery"] >= 0.7)
        weak = sum(1 for v in bkt_summary.values() if v["mastery"] < 0.5)
        metrics["已掌握(≥70%)"] = f"{mastered}/5"
        metrics["薄弱(<50%)"] = f"{weak}/5"

        # 趋势图标
        level = "success" if "improving" in trend else "danger" if "declining" in trend else "info"

        summary = f"{subject}第{self.week}周：作业{n}次，当前正确率{latest.accuracy:.1%}。"
        if "improving" in trend:
            summary += " 整体呈上升趋势，继续保持。"
        elif "declining" in trend:
            summary += " 呈下降趋势，需要关注。"
        else:
            summary += " 趋势平稳。"
        summary += f" 已掌握{mastered}/5类错误，{weak}/5类仍需加强。"

        return ReportSection(
            title=f"📊 {subject}第{self.week}周概览",
            summary=summary,
            metrics=metrics,
            level=level,
        )

    def _build_mastery(
        self,
        bkt_summary: dict[str, dict],
        error_tracking: dict[str, str],
    ) -> ReportSection:
        items = []
        for e_type in ErrorType:
            info = bkt_summary.get(e_type.value, {})
            mastery = info.get("mastery", 0)
            obs = info.get("observations", 0)
            status = error_tracking.get(e_type.value, "no_change")

            level_label = info.get("level", "未知")
            status_cn = _tracking_status_cn(status)

            # 图标
            icon = "✅" if mastery >= 0.7 else "⚠️" if mastery >= 0.5 else "🔴"
            items.append({
                "error_type": _error_type_cn(e_type.value),
                "mastery": f"{mastery:.1%}",
                "level": level_label,
                "observations": obs,
                "trend": status_cn,
                "icon": icon,
            })

        # 总结
        mastered = [i for i in items if "✅" in i["icon"]]
        weak = [i for i in items if "🔴" in i["icon"]]
        summary_parts = []
        if mastered:
            summary_parts.append(f"已掌握：{'、'.join(i['error_type'] for i in mastered)}")
        if weak:
            summary_parts.append(f"需加强：{'、'.join(i['error_type'] for i in weak)}")
        summary = "；".join(summary_parts) if summary_parts else "数据不足"

        return ReportSection(
            title="🧠 BKT掌握度分析",
            summary=summary,
            items=items,
            level="info",
        )

    def _build_errors(
        self,
        error_patterns: list[ErrorPatternStats],
        error_tracking: dict[str, str],
        bkt_summary: dict[str, dict],
    ) -> ReportSection:
        items = []
        warnings = []

        for p in error_patterns:
            status = error_tracking.get(p.error_type, "no_change")
            mastery = bkt_summary.get(p.error_type, {}).get("mastery", 0.5)

            # 判断严重程度
            severity = "low"
            if status in ("worsening", "new") and mastery < 0.5:
                severity = "high"
                warnings.append(_error_type_cn(p.error_type))
            elif status in ("worsening", "new"):
                severity = "medium"

            items.append({
                "error_type": _error_type_cn(p.error_type),
                "this_week": p.this_week_count,
                "last_week": p.last_week_count,
                "trend": _tracking_status_cn(status),
                "severity": severity,
                "self_correction": f"{p.self_correction_rate:.0%}" if p.self_correction_rate is not None else "N/A",
            })

        summary = "本周错误分析："
        if warnings:
            summary += f"重点关注：{'、'.join(warnings)}（恶化中且掌握度低）。"
        else:
            worsening = [p for p in error_patterns if error_tracking.get(p.error_type) == "worsening"]
            if worsening:
                summary += f"异常：{'、'.join(_error_type_cn(p.error_type) for p in worsening)}呈恶化趋势。"
            else:
                summary += "各类错误无恶化趋势。"
        summary += f" 共分析{len(error_patterns)}类错误。"

        return ReportSection(
            title="🐛 错误分析",
            summary=summary,
            items=items,
            level="danger" if warnings else "info",
        )

    def _build_rules(
        self,
        rules: list[RuleOutput],
        series: SeriesStats,
        error_patterns: list[ErrorPatternStats],
    ) -> ReportSection:
        if not rules:
            return ReportSection(
                title="📋 规则结论",
                summary="本次分析无规则触发（数据不足或无明显模式）。",
                level="info",
            )

        # 按置信度分组
        high = [r for r in rules if r.confidence == "high"]
        medium = [r for r in rules if r.confidence == "medium"]
        low = [r for r in rules if r.confidence == "low"]

        # 统计
        n_positive = sum(1 for r in rules if r.rule_id in ("R001", "R003", "R007"))
        n_negative = sum(1 for r in rules if r.rule_id in ("R002", "R004", "R005", "R006", "R008"))

        # 摘要
        parts = []
        if high:
            parts.append(f"高置信度{len(high)}条")
        if medium:
            parts.append(f"中置信度{len(medium)}条")
        if n_positive:
            parts.append(f"正面{n_positive}条")
        if n_negative:
            parts.append(f"负面{n_negative}条")
        summary = f"触发{len(rules)}条规则：{'、'.join(parts)}。"

        items = [r.to_dict() for r in rules]
        level = "danger" if n_negative > n_positive else "warning" if n_negative > 0 else "success"

        return ReportSection(
            title="📋 规则结论",
            summary=summary,
            items=items,
            level=level,
        )

    def _build_suggestions(
        self,
        error_patterns: list[ErrorPatternStats],
        bkt_summary: dict[str, dict],
        rules: list[RuleOutput],
        error_tracking: dict[str, str],
        subject: str,
    ) -> ReportSection:
        """根据错误类型 + 掌握度生成学习建议。"""
        suggestions = []

        for p in error_patterns:
            mastery = bkt_summary.get(p.error_type, {}).get("mastery", 0.5)
            status = error_tracking.get(p.error_type, "no_change")

            if p.error_type == ErrorType.CARELESS.value:
                if mastery < 0.7:
                    suggestions.append(SuggestionRow(
                        problem="粗心/习惯",
                        severity="medium" if mastery < 0.5 else "low",
                        suggestion="增加检查环节，做完后逐题回看。建立错题本，标注粗心原因。",
                        frequency="每日作业后5分钟检查",
                        expected="2-3周后粗心率下降50%",
                    ))
            elif p.error_type == ErrorType.CONCEPT.value:
                if mastery < 0.7:
                    suggestions.append(SuggestionRow(
                        problem="概念不清",
                        severity="high" if mastery < 0.5 else "medium",
                        suggestion="回归课本，重做概念例题。使用思维导图梳理知识结构。",
                        frequency="每周2次专题复习",
                        expected="4周后掌握度提升至70%以上",
                    ))
            elif p.error_type == ErrorType.CALCULATION.value:
                if mastery < 0.7:
                    suggestions.append(SuggestionRow(
                        problem="计算失误",
                        severity="medium",
                        suggestion="每日5分钟口算/笔算训练。大题先列竖式再动笔。",
                        frequency="每日5分钟计算专项",
                        expected="2周后计算失误减少",
                    ))
            elif p.error_type == ErrorType.METHOD.value:
                if mastery < 0.7:
                    suggestions.append(SuggestionRow(
                        problem="方法错误",
                        severity="high" if mastery < 0.5 else "medium",
                        suggestion="分析标准解题步骤，对比自己的解题思路差异。",
                        frequency="每周分析3道典型题",
                        expected="3周后方法类错误减少",
                    ))
            elif p.error_type == ErrorType.READING.value:
                if mastery < 0.7:
                    suggestions.append(SuggestionRow(
                        problem="审题错误",
                        severity="medium",
                        suggestion="读题时圈关键词，做完检查是否答非所问。",
                        frequency="每次作业前3题慢读2遍",
                        expected="1-2周后审题错误明显减少",
                    ))

        # 检查是否有 R005 难题放弃
        has_r005 = any(r.rule_id == "R005" for r in rules)
        if has_r005:
            suggestions.append(SuggestionRow(
                problem="难题畏难/放弃",
                severity="high",
                suggestion="先做会做的，最后再攻克难题。即使不会也要写出已知条件和解题思路，得步骤分。",
                frequency="每次考试/作业都尝试",
                expected="逐步建立攻克难题的信心",
            ))

        if not suggestions:
            summary = "当前无明显薄弱环节，继续保持。"
        else:
            high_count = sum(1 for s in suggestions if s.severity == "high")
            summary = f"发现{len(suggestions)}条建议，其中{high_count}条高优先级。"

        items = [asdict(s) for s in suggestions]

        return ReportSection(
            title="💡 学习建议",
            summary=summary,
            items=items,
            level="danger" if any(s.severity == "high" for s in suggestions) else "info",
        )

    def _build_plan(
        self,
        error_patterns: list[ErrorPatternStats],
        bkt_summary: dict[str, dict],
        error_tracking: dict[str, str],
        subject: str,
    ) -> ReportSection:
        """生成下周练习计划。"""
        # 找出薄弱环节
        weak_types = [
            e.value for e in ErrorType
            if bkt_summary.get(e.value, {}).get("mastery", 0.5) < 0.6
        ]

        # 按优先级排序
        priority = ["concept", "calculation", "method", "careless", "reading"]
        weak_sorted = [et for et in priority if et in weak_types]

        if not weak_sorted:
            return ReportSection(
                title="📅 下周计划",
                summary="各类错误掌握度良好，建议维持现有学习节奏，定期复习巩固。",
                level="success",
            )

        # 生成计划安排
        day_tasks = []
        days_of_week = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

        # 分配薄弱环节到不同天
        for i, et in enumerate(weak_sorted[:5]):
            day = days_of_week[i % 7]
            task, purpose = _plan_for_error_type(et)
            day_tasks.append(PlanItem(
                day=day,
                subject=subject,
                task=task,
                purpose=purpose,
            ))

        # 剩余的天填巩固
        for i in range(len(weak_sorted), len(days_of_week)):
            day = days_of_week[i % 7]
            day_tasks.append(PlanItem(
                day=day,
                subject=subject,
                task="完成当日作业 + 错题回顾",
                purpose="巩固已学知识，防止旧错复发",
            ))

        summary = f"下周重点：针对{'、'.join(_error_type_cn(et) for et in weak_sorted)}进行专项训练。"

        items = [asdict(p) for p in day_tasks]

        return ReportSection(
            title="📅 下周计划",
            summary=summary,
            items=items,
            level="info",
        )

    def _build_weekly_report(
        self,
        sections: list[ReportSection],
        series: SeriesStats,
        error_patterns: list[ErrorPatternStats],
        homework_stats: list[HomeworkStats],
    ) -> WeeklyReport:
        """构建兼容 data_models.WeeklyReport 的结构化数据。"""
        recent_problems = []
        tracking_items = []
        all_suggestions = []

        # 从 sections 提取数据
        for section in sections:
            if section.title.startswith("🐛"):
                for item in section.items:
                    recent_problems.append(RecurringProblem(
                        problem=item.get("error_type", ""),
                        frequency=f"本周{item.get('this_week', 0)}次",
                        trend=item.get("trend", ""),
                    ))
            if section.title.startswith("🧠"):
                for item in section.items:
                    tracking_items.append(TrackingItem(
                        problem=item.get("error_type", ""),
                        last_week=0,
                        this_week=0,
                        status=item.get("trend", ""),
                    ))
            if section.title.startswith("💡"):
                for item in section.items:
                    all_suggestions.append(SuggestionRow(
                        problem=item.get("problem", ""),
                        severity=item.get("severity", "low"),
                        suggestion=item.get("suggestion", ""),
                        frequency=item.get("frequency", ""),
                        expected=item.get("expected", ""),
                    ))

        sections_dict = {
            "overview": sections[0].to_dict() if sections else {},
            "mastery": sections[1].to_dict() if len(sections) > 1 else {},
            "errors": sections[2].to_dict() if len(sections) > 2 else {},
            "rules": sections[3].to_dict() if len(sections) > 3 else {},
            "suggestions": sections[4].to_dict() if len(sections) > 4 else {},
            "plan": sections[5].to_dict() if len(sections) > 5 else {},
        }

        return WeeklyReport(
            student_id=self.student_id,
            week=self.week,
            date_range=self.date_range,
            homework_count=len(homework_stats),
            sections=sections_dict,
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _error_type_cn(et: str) -> str:
    mapping = {
        "careless": "粗心/习惯",
        "concept": "概念不清",
        "calculation": "计算失误",
        "method": "方法错误",
        "reading": "审题错误",
    }
    return mapping.get(et, et)


def _trend_cn(trend: str) -> str:
    mapping = {
        "improving": "上升 ↑",
        "declining": "下降 ↓",
        "stable": "平稳 →",
        "volatile": "波动大 ~",
        "insufficient_data": "数据不足",
    }
    for k, v in mapping.items():
        if k in trend:
            return v
    return trend


def _tracking_status_cn(status: str) -> str:
    mapping = {
        "improving": "改善中",
        "resolved": "已解决",
        "no_change": "无变化",
        "worsening": "恶化中",
        "new": "新出现",
    }
    return mapping.get(status, status)


def _plan_for_error_type(et: str) -> tuple[str, str]:
    """根据错误类型返回建议的练习任务和目的。"""
    plans = {
        "careless": ("完成5道易错题，逐题检查并标注粗心点", "训练检查习惯，减少粗心失误"),
        "concept": ("做一张本章概念思维导图 + 3道概念辨析题", "加深概念理解，建立知识体系"),
        "calculation": ("10道计算专项练习（口算+笔算混合）", "提高计算准确率和速度"),
        "method": ("分析3道典型题的多种解法，对比差异", "拓展解题思路，优化方法选择"),
        "reading": ("3道读题训练：慢读2遍→圈关键词→复述题目", "培养审题习惯，减少审题错误"),
    }
    return plans.get(et, ("复习本周错题", "巩固薄弱环节"))