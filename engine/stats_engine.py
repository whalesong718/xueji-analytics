"""
学业量化分析引擎 — 统计算法层。

接收 Homework 列表，输出：
1. 单次作业统计（正确率、完成率、难题正确率、错误分布）
2. 时间序列统计（移动平均、EWMA、趋势斜率、稳定性）
3. 错误模式统计（重复率、自纠率、周对比）
4. 综合趋势判定

纯标准库，无第三方依赖。
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from engine.data_models import (
    ErrorType,
    ErrorPatternStats,
    Homework,
    HomeworkStats,
    SeriesStats,
    TrackingStatus,
    compute_summary_from_questions,
)


def compute_homework_stats(hw: Homework) -> HomeworkStats:
    """计算单次作业的统计指标。"""
    questions = hw.questions
    total = len(questions)
    correct = sum(1 for q in questions if q.correct is True)
    wrong = sum(1 for q in questions if q.correct is False)
    blank = sum(1 for q in questions if q.correct is None)
    answered = correct + wrong

    # 正确率（空题不计入分母）
    accuracy = round(correct / answered, 4) if answered else 0.0

    # 完成率
    completion = round(answered / total, 4) if total else 0.0

    # 难题正确率
    hard = [q for q in questions if q.difficulty == "hard"]
    hard_answered = [q for q in hard if q.correct is not None]
    hard_correct = sum(1 for q in hard_answered if q.correct is True)
    hard_rate = round(hard_correct / len(hard_answered), 4) if hard_answered else 0.0

    # 错误类型统计
    error_counts: dict[str, int] = {e.value: 0 for e in ErrorType}
    for q in questions:
        if q.correct is False and q.error_type:
            if q.error_type in error_counts:
                error_counts[q.error_type] += 1
            else:
                error_counts[q.error_type] = error_counts.get(q.error_type, 0) + 1

    error_share = {}
    total_errors = sum(error_counts.values())
    if total_errors:
        error_share = {k: round(v / total_errors, 4) for k, v in error_counts.items()}

    # 难题空题数
    blank_hard = sum(1 for q in hard if q.correct is None)

    return HomeworkStats(
        homework_id=hw.homework_id,
        date=hw.date,
        accuracy=accuracy,
        completion=completion,
        hard_correct_rate=hard_rate,
        error_type_counts=error_counts,
        error_type_share=error_share,
        blank_hard_count=blank_hard,
        total_questions=total,
        correct_count=correct,
        wrong_count=wrong,
        blank_count=blank,
    )


# ---------------------------------------------------------------------------
# 时间序列统计
# ---------------------------------------------------------------------------


def compute_series_stats(
    stats_list: list[HomeworkStats],
    window: int = 5,
) -> SeriesStats:
    """计算时间序列统计。

    Args:
        stats_list: 按时间排序的 HomeworkStats 列表。
        window: 移动平均窗口（默认5，即近一周5次作业）。

    Returns:
        SeriesStats
    """
    accuracies = [s.accuracy for s in stats_list]
    n = len(accuracies)

    # 简单移动平均
    if n >= window:
        moving_avg = round(sum(accuracies[-window:]) / window, 4)
    else:
        moving_avg = None

    # 指数加权移动平均（EWMA，alpha=0.3）
    if n >= 1:
        alpha = 0.3
        ewma = accuracies[0]
        for a in accuracies[1:]:
            ewma = alpha * a + (1 - alpha) * ewma
        ewma = round(ewma, 4)
    else:
        ewma = None

    # 趋势斜率：最近 5 次/全量的线性回归
    if n >= 2:
        # 用最近 window 次或全部
        points = min(window, n)
        x = list(range(points))
        y = accuracies[-points:]
        slope = _linear_regression_slope(x, y)
        progress_slope = round(slope, 6)
    else:
        progress_slope = None

    # 稳定性：方差（越小越好）
    if n >= 2:
        mean = sum(accuracies) / n
        variance = sum((a - mean) ** 2 for a in accuracies) / n
        stability_variance = round(variance, 6)
    else:
        stability_variance = None

    # 连续改善/退步计数
    consecutive_improving = 0
    consecutive_declining = 0
    for i in range(n - 1, 0, -1):
        if accuracies[i] > accuracies[i - 1]:
            consecutive_improving += 1
            consecutive_declining = 0
        elif accuracies[i] < accuracies[i - 1]:
            consecutive_declining += 1
            consecutive_improving = 0
        else:
            break

    return SeriesStats(
        window=window,
        accuracies=accuracies,
        moving_avg=moving_avg,
        ewma=ewma,
        progress_slope=progress_slope,
        stability_variance=stability_variance,
        consecutive_improving=consecutive_improving,
        consecutive_declining=consecutive_declining,
    )


def _linear_regression_slope(x: list[float], y: list[float]) -> float:
    """最小二乘法计算斜率。"""
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(x[i] * y[i] for i in range(n))
    sum_xx = sum(xi * xi for xi in x)
    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-10:
        return 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denom
    return slope


# ---------------------------------------------------------------------------
# 错误模式统计
# ---------------------------------------------------------------------------


def compute_error_pattern_stats(
    stats_list: list[HomeworkStats],
    window: int = 5,
) -> list[ErrorPatternStats]:
    """计算每种错误类型的模式统计。

    Args:
        stats_list: 按时间排序的 HomeworkStats 列表。
        window: 观察窗口（默认5次作业）。

    Returns:
        每类错误一个 ErrorPatternStats。
    """
    # 收集每类错误每次作业的出现次数
    error_series: dict[str, list[int]] = {e.value: [] for e in ErrorType}
    for s in stats_list:
        for e in ErrorType:
            error_series[e.value].append(s.error_type_counts.get(e.value, 0))

    results = []
    for e in ErrorType:
        series = error_series[e.value]
        n = len(series)

        # 近 window 次
        recent = series[-window:] if n >= window else series

        # 重复率：近 window 次总错误次数 / window
        repeat_rate = round(sum(recent) / window, 4) if window else 0.0

        # 出现次数：近 window 次中有几次不等于0
        occurrence_count = sum(1 for v in recent if v > 0)

        # 周对比：last_week vs this_week（各5次）
        if n >= 10:
            last_week_count = sum(series[-10:-5])
            this_week_count = sum(series[-5:])
        elif n >= 5:
            last_week_count = sum(series[:-5]) if n > 5 else 0
            this_week_count = sum(series[-5:])
        else:
            last_week_count = 0
            this_week_count = sum(series)

        # 自纠率：同一类错误，下次作业是否减少
        self_correction_rate = _compute_self_correction_rate(e.value, series)

        results.append(ErrorPatternStats(
            error_type=e.value,
            counts_per_homework=series,
            repeat_rate=repeat_rate,
            occurrence_count=occurrence_count,
            last_week_count=last_week_count,
            this_week_count=this_week_count,
            self_correction_rate=self_correction_rate,
        ))

    return results


def _compute_self_correction_rate(
    error_type: str,
    series: list[int],
) -> Optional[float]:
    """自纠率：错误出现后，下一次作业该错误减少的比例。

    计算方式：对有错误的作业，看下一次该错误是否减少。
    返回减少的比例（0-1）。
    """
    if len(series) < 2:
        return None

    improvements = 0
    opportunities = 0
    for i in range(len(series) - 1):
        if series[i] > 0:
            opportunities += 1
            if series[i + 1] < series[i]:
                improvements += 1

    return round(improvements / opportunities, 4) if opportunities else None


# ---------------------------------------------------------------------------
# 趋势判定
# ---------------------------------------------------------------------------


def determine_trend(series: SeriesStats) -> str:
    """综合判定趋势方向。

    Returns:
        "improving", "stable", "declining", "volatile", "insufficient_data"
    """
    if series.progress_slope is None:
        return "insufficient_data"

    # 斜率 > 0.01 且 EWMA 上升
    improving = series.progress_slope > 0.01
    declining = series.progress_slope < -0.01

    # 稳定性：方差 > 0.02 视为波动大
    volatile = series.stability_variance is not None and series.stability_variance > 0.02

    if volatile:
        if improving:
            return "improving (volatile)"
        if declining:
            return "declining (volatile)"
        return "volatile"

    if improving:
        return "improving"
    if declining:
        return "declining"
    return "stable"


def determine_error_tracking_status(
    pattern: ErrorPatternStats,
    threshold_increase: float = 0.3,
) -> str:
    """判定单个错误类型的追踪状态。

    Args:
        pattern: 错误模式统计。
        threshold_increase: 周增加阈值（默认30%）。

    Returns:
        TrackingStatus 值。
    """
    if pattern.this_week_count == 0 and pattern.last_week_count == 0:
        return TrackingStatus.NO_CHANGE.value

    if pattern.this_week_count == 0:
        return TrackingStatus.RESOLVED.value

    if pattern.last_week_count == 0 and pattern.this_week_count > 0:
        return TrackingStatus.NEW.value

    # 周对比
    if pattern.last_week_count > 0:
        change = (pattern.this_week_count - pattern.last_week_count) / pattern.last_week_count
        if change < -threshold_increase:
            return TrackingStatus.IMPROVING.value
        elif change > threshold_increase:
            return TrackingStatus.WORSENING.value

    return TrackingStatus.NO_CHANGE.value


# ---------------------------------------------------------------------------
# 批量处理
# ---------------------------------------------------------------------------


def analyze_homework_series(
    homeworks: list[Homework],
    window: int = 5,
) -> dict:
    """分析一组作业，输出完整统计结果。

    Args:
        homeworks: 按时间排序的 Homework 列表。
        window: 移动平均窗口。

    Returns:
        {
            "homework_stats": [HomeworkStats, ...],
            "series_stats": SeriesStats,
            "error_patterns": [ErrorPatternStats, ...],
            "trend": str,
            "error_tracking": {error_type: tracking_status, ...},
        }
    """
    stats_list = [compute_homework_stats(hw) for hw in homeworks]
    series_stats = compute_series_stats(stats_list, window=window)
    error_patterns = compute_error_pattern_stats(stats_list, window=window)
    trend = determine_trend(series_stats)

    error_tracking = {}
    for p in error_patterns:
        error_tracking[p.error_type] = determine_error_tracking_status(p)

    return {
        "homework_count": len(homeworks),
        "homework_stats": stats_list,
        "series_stats": series_stats,
        "error_patterns": error_patterns,
        "trend": trend,
        "error_tracking": error_tracking,
    }


def analyze_weekly(
    homeworks: list[Homework],
    window: int = 5,
) -> dict:
    """按周分组分析。

    Returns:
        {week: analyze_homework_series(...), ...}
    """
    by_week: dict[int, list[Homework]] = defaultdict(list)
    for hw in homeworks:
        if hw.week is not None:
            by_week[hw.week].append(hw)

    # 按周排序
    result = {}
    for week in sorted(by_week):
        result[week] = analyze_homework_series(
            by_week[week], window=min(window, len(by_week[week]))
        )

    # 累积分析（逐周累积）
    all_hws = []
    cumulative = {}
    for week in sorted(by_week):
        all_hws.extend(by_week[week])
        cumulative[week] = analyze_homework_series(
            all_hws, window=window
        )

    return {
        "weekly": result,
        "cumulative": cumulative,
    }