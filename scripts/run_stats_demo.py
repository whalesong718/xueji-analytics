"""验证脚本：统计算法 + BKT + 规则引擎 完整链路。

用法：
  python3 scripts/run_stats_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.data_models import Homework
from engine.stats_engine import (
    analyze_homework_series,
    analyze_weekly,
)
from engine.bkt_engine import run_bkt_sequence, summarize_bkt
from engine.report_generator import ReportGenerator
from engine.rules_engine import run_rules, format_rule_summary


def load_data(path: str) -> list[Homework]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    homeworks = [Homework.from_dict(h) for h in raw["homeworks"]]
    meta = raw.get("meta", {})
    print(f"📊 加载数据集: {meta.get('description', 'N/A')}")
    print(f"   学生: {meta.get('student_id', 'N/A')}")
    print(f"   总作业数: {len(homeworks)}")
    print()
    return homeworks


def print_series_stats(series, label: str):
    print(f"\n📈 {label} 趋势分析:")
    mov = f"{series.moving_avg:.1%}" if series.moving_avg is not None else "数据不足"
    ewma = f"{series.ewma:.1%}" if series.ewma is not None else "数据不足"
    slope = f"{series.progress_slope:.6f}" if series.progress_slope is not None else "数据不足"
    var = f"{series.stability_variance:.6f}" if series.stability_variance is not None else "数据不足"
    print(f"   移动平均({series.window}次): {mov}")
    print(f"   EWMA: {ewma}")
    print(f"   趋势斜率: {slope}")
    print(f"   稳定性(方差): {var}")
    print(f"   连续改善: {series.consecutive_improving}次  连续退步: {series.consecutive_declining}次")


def print_patterns(patterns, label):
    print(f"\n🐛 {label} 错误模式:")
    for p in patterns:
        rate = f"{p.self_correction_rate:.1%}" if p.self_correction_rate is not None else "N/A"
        print(f"   {p.error_type}: "
              f"重复率={p.repeat_rate:.2f} "
              f"出现(近5次)={p.occurrence_count}/5 "
              f"上周={p.last_week_count}→本周={p.this_week_count} "
              f"自纠率={rate}")


def main():
    data_path = Path(__file__).resolve().parent.parent / "data" / "mock_data.json"
    homeworks = load_data(str(data_path))

    # ====== Phase 1: 统计算法 ======
    print("=" * 60)
    print("Phase 1: 统计算法层")
    print("=" * 60)

    results = analyze_homework_series(homeworks, window=5)
    print(f"\n📊 总作业数: {results['homework_count']}")
    print(f"📈 趋势判定: {results['trend']}")

    print_series_stats(results['series_stats'], "整体")
    print_patterns(results['error_patterns'], "整体")

    print(f"\n🐛 错误追踪状态:")
    for e, status in results['error_tracking'].items():
        print(f"   {e}: {status}")

    # ====== Phase 2: BKT 掌握度 ======
    print("\n" + "=" * 60)
    print("Phase 2a: BKT 掌握度模型")
    print("=" * 60)

    bkt_engine = run_bkt_sequence(homeworks)
    bkt_summary = summarize_bkt(bkt_engine)

    print(f"\n📊 BKT 掌握度（按错误类型，20次作业后）:")
    for e_type, info in bkt_summary.items():
        print(f"   {e_type}: 掌握度={info['mastery']:.1%} 等级={info['level']} "
              f"观察次数={info['observations']}")

    # 展示掌握度变化曲线
    print(f"\n📈 掌握度变化曲线:")
    for e_type in bkt_summary:
        state = bkt_engine.states[e_type]
        points = [h["mastery"] for h in state.mastery_history]
        # 取关键点显示
        if points:
            start = points[0]
            end = points[-1]
            diff = end - start
            arrow = "↑" if diff > 0.05 else ("↓" if diff < -0.05 else "→")
            print(f"   {e_type}: {start:.1%} → {end:.1%} {arrow} ({len(points)}次更新)")

    # ====== Phase 2: 规则引擎 ======
    print("\n" + "=" * 60)
    print("Phase 2b: 规则引擎")
    print("=" * 60)

    # 整体分析
    triggered = run_rules(
        series=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        bkt_summary=bkt_summary,
        homework_stats=results["homework_stats"],
        subject="数学",
    )

    print(f"\n📋 规则触发（整体）:")
    if triggered:
        for r in triggered:
            conf_icon = {"high": "🟢", "medium": "🟡", "low": "⚪"}.get(r.confidence, "⚪")
            print(f"   {conf_icon} [{r.rule_id}] {r.rule_name}: {r.format_message()}")
    else:
        print(f"   无规则触发")

    # 每周分别跑规则
    print(f"\n📅 每周规则触发:")
    weekly = analyze_weekly(homeworks, window=5)
    weekly_bkts = {}

    for week in sorted(weekly["weekly"]):
        week_data = weekly["weekly"][week]
        # 对该周作业跑BKT
        week_hws = [h for h in homeworks if h.week == week]
        week_bkt = run_bkt_sequence(week_hws)
        weekly_bkts[week] = summarize_bkt(week_bkt)

        week_rules = run_rules(
            series=week_data["series_stats"],
            error_patterns=week_data["error_patterns"],
            error_tracking=week_data["error_tracking"],
            bkt_summary=weekly_bkts[week],
            homework_stats=week_data["homework_stats"],
            subject="数学",
        )
        print(f"\n   第{week}周:")
        if week_rules:
            for r in week_rules:
                print(f"     [{r.rule_id}] {r.format_message()}")
        else:
            print(f"     无规则触发")

    # ====== 摘要 ======
    print("\n" + "=" * 60)
    print("📋 综合摘要")
    print("=" * 60)

    # 近5次
    last5 = homeworks[-5:]
    last5_analysis = analyze_homework_series(last5, window=5)
    last5_bkt = run_bkt_sequence(last5)
    last5_bkt_summary = summarize_bkt(last5_bkt)
    last5_rules = run_rules(
        series=last5_analysis["series_stats"],
        error_patterns=last5_analysis["error_patterns"],
        error_tracking=last5_analysis["error_tracking"],
        bkt_summary=last5_bkt_summary,
        homework_stats=last5_analysis["homework_stats"],
        subject="数学",
    )

    print(f"\n📈 近5次作业趋势: {last5_analysis['trend']}")
    mov = f"{last5_analysis['series_stats'].moving_avg:.1%}" if last5_analysis['series_stats'].moving_avg is not None else "N/A"
    print(f"   移动平均: {mov}")

    print(f"\n📊 BKT掌握度:")
    for e, info in last5_bkt_summary.items():
        print(f"   {e}: {info['mastery']:.1%} ({info['level']})")

    print(f"\n📋 规则结论:")
    if last5_rules:
        for r in last5_rules:
            print(f"   [{r.rule_id}] {r.format_message()}")
    else:
        print(f"   无规则触发（数据不足）")

    # ====== Phase 3: 报告生成 ======
    print("\n" + "=" * 60)
    print("Phase 3: 报告生成层")
    print("=" * 60)

    from datetime import datetime

    # 为第4周生成报告（整体数据）
    generator = ReportGenerator(
        student_id="student_001",
        week=4,
        date_range="第13-16天",
    )

    report = generator.generate(
        series_stats=results["series_stats"],
        error_patterns=results["error_patterns"],
        error_tracking=results["error_tracking"],
        trend=results["trend"],
        homework_stats=results["homework_stats"],
        bkt_summary=bkt_summary,
        rules=triggered,
        subject="数学",
    )

    # 输出文本报告
    print(report.format_text())

    # 每周报告
    print("\n" + "=" * 60)
    print("📅 每周报告摘要")
    print("=" * 60)

    for week in sorted(weekly["weekly"]):
        week_data = weekly["weekly"][week]
        week_bkt = weekly_bkts.get(week, {})
        week_rules = run_rules(
            series=week_data["series_stats"],
            error_patterns=week_data["error_patterns"],
            error_tracking=week_data["error_tracking"],
            bkt_summary=week_bkt,
            homework_stats=week_data["homework_stats"],
            subject="数学",
        )

        # 生成每周报告
        from datetime import datetime

        week_gen = ReportGenerator(
            student_id="student_001",
            week=week,
            date_range=f"第{week}周",
        )
        week_report = week_gen.generate(
            series_stats=week_data["series_stats"],
            error_patterns=week_data["error_patterns"],
            error_tracking=week_data["error_tracking"],
            trend=week_data["trend"],
            homework_stats=week_data["homework_stats"],
            bkt_summary=week_bkt,
            rules=week_rules,
            subject="数学",
        )

        # 只输出概览摘要
        overview = week_report.sections[0]
        mastery = week_report.sections[1]
        print(f"  第{week}周: {overview.metrics.get('当前正确率', 'N/A')} "
              f"| 趋势: {overview.metrics.get('趋势', 'N/A')} "
              f"| 掌握: {overview.metrics.get('已掌握(≥70%)', 'N/A')} "
              f"| 薄弱: {overview.metrics.get('薄弱(<50%)', 'N/A')}")

    print(f"\n{'=' * 30}")
    print("Phase 3 验证完成 ✅")


if __name__ == "__main__":
    main()