"""生成4周×5次=20次模拟作业数据，写入 data/mock_data.json。

设计意图：
- 1个学生（id: student_001）
- 四年级数学
- 每周5次作业，每次10-15题
- 错误类型随时间呈现可控趋势（粗心→改善，概念→波动，计算→反复）
- 题目难度分布：易40% 中40% 难20%
- 题型：计算、应用题、概念、填空、选择
"""

import json
import random
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STUDENT_ID = "student_001"
SUBJECT = "math"
GRADE = 4

# 每周的作弊表：控制每类错误每次作业出现次数（min, max）
# 设计目标正确率：第1周55-60% → 第4周70-78%
# 每次作业10-15题，故错误数需从5-8降到2-4
WEEK_ERROR_BUDGET = {
    # 计算失误持续减少，概念从有到无再到新问题，审题渐少
    1: {"careless": (2, 3), "concept": (1, 2), "calculation": (2, 3), "method": (0, 1), "reading": (1, 1)},
    2: {"careless": (1, 2), "concept": (1, 2), "calculation": (1, 2), "method": (0, 1), "reading": (0, 1)},
    3: {"careless": (1, 1), "concept": (0, 0), "calculation": (1, 2), "method": (0, 0), "reading": (0, 1)},
    4: {"careless": (0, 1), "concept": (1, 2), "calculation": (0, 1), "method": (0, 0), "reading": (0, 0)},
    # 概念第3周解决（0次），第4周新概念出现（1-2次）
}

ERROR_TYPES = list(WEEK_ERROR_BUDGET[1].keys())
DIFFICULTIES = ["easy", "medium", "hard"]
DIFFICULTY_WEIGHTS = [0.4, 0.4, 0.2]
QUESTION_TYPES = ["calculation", "word_problem", "concept", "fill_blank", "choice"]
QUESTION_TYPES_WEIGHTS = [0.3, 0.25, 0.2, 0.15, 0.1]

# 错误类型→常见题型映射（用于自然感）
ERROR_TYPE_QUESTION_MAP = {
    "careless": ["calculation", "fill_blank", "choice"],
    "concept": ["concept", "choice", "fill_blank"],
    "calculation": ["calculation", "word_problem"],
    "method": ["word_problem", "concept"],
    "reading": ["word_problem", "fill_blank"],
}


def generate_homework(week: int, hw_index: int) -> dict:
    """生成一次作业。"""
    date = f"2026-0{3 + (week - 1) // 4}-{1 + (week - 1) % 4 * 7 + hw_index:02d}"
    n_questions = random.randint(10, 15)
    hw_id = f"hw_w{week:02d}_{hw_index:02d}"

    budget = WEEK_ERROR_BUDGET[week]
    questions = []
    error_tracker = {e: 0 for e in ERROR_TYPES}

    # 先分配题目类型和难度，再决定对错
    for q_num in range(1, n_questions + 1):
        q_type = random.choices(QUESTION_TYPES, weights=QUESTION_TYPES_WEIGHTS, k=1)[0]
        difficulty = random.choices(DIFFICULTIES, weights=DIFFICULTY_WEIGHTS, k=1)[0]

        # 决定是否犯错：根据难度和剩余错误预算
        # 基础正确率：易 90% 中 75% 难 50%
        base_correct_rate = {"easy": 0.90, "medium": 0.75, "hard": 0.50}[difficulty]

        # 看还有多少错误额度没用，调整犯错概率
        remaining_budget = sum(
            max(0, budget[e][0] - error_tracker[e]) for e in ERROR_TYPES
        )
        remaining_questions = n_questions - q_num + 1
        if remaining_questions > 0:
            needed_flip_rate = remaining_budget / remaining_questions
            error_rate = max(0, needed_flip_rate)
        else:
            error_rate = 1 - base_correct_rate

        # 综合正确率
        correct_prob = base_correct_rate * (1 - error_rate * 0.5)
        is_correct = random.random() < correct_prob

        question = {
            "q_num": q_num,
            "type": q_type,
            "correct": is_correct,
            "difficulty": difficulty,
            "error_type": None,
            "error_detail": None,
            "confidence": 1.0,
            "source": "mock",
        }

        if not is_correct:
            # 分配错误类型：优先选还有预算的
            eligible = [
                e for e in ERROR_TYPES
                if error_tracker[e] < budget[e][1]
            ]
            if not eligible:
                eligible = ERROR_TYPES  # 超预算也照常分配
            err_type = random.choices(eligible, weights=[2 if error_tracker[e] < budget[e][0] else 1 for e in eligible], k=1)[0]
            error_tracker[err_type] += 1
            question["error_type"] = err_type
            question["error_detail"] = _error_detail(err_type, q_type)

        questions.append(question)

    # 计算统计摘要
    correct_count = sum(1 for q in questions if q["correct"] is True)
    wrong_count = sum(1 for q in questions if q["correct"] is False)
    blank_count = sum(1 for q in questions if q["correct"] is None)
    answered = correct_count + wrong_count
    accuracy = round(correct_count / answered, 4) if answered else 0.0
    completion = round(answered / n_questions, 4)

    hard_qs = [q for q in questions if q["difficulty"] == "hard"]
    hard_answered = [q for q in hard_qs if q["correct"] is not None]
    hard_correct = sum(1 for q in hard_answered if q["correct"] is True)
    hard_rate = round(hard_correct / len(hard_answered), 4) if hard_answered else 0.0

    error_dist = {e: 0 for e in ERROR_TYPES}
    for q in questions:
        if q["correct"] is False and q["error_type"]:
            error_dist[q["error_type"]] += 1

    return {
        "student_id": STUDENT_ID,
        "date": date,
        "subject": SUBJECT,
        "grade": GRADE,
        "homework_id": hw_id,
        "total_questions": n_questions,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "blank_count": blank_count,
        "questions": questions,
        "summary": {
            "accuracy": accuracy,
            "completion": completion,
            "hard_correct_rate": hard_rate,
            "error_type_distribution": error_dist,
        },
        "week": week,
    }


def _error_detail(err_type: str, q_type: str) -> str:
    """生成自然的错误描述。"""
    details = {
        "careless": [
            "抄错数字", "漏写负号", "单位遗漏", "进位错误", "小数点错位",
            "看错运算符", "抄错题目", "忘记写答案",
        ],
        "concept": [
            "公式记混", "概念理解偏差", "定理适用错误", "定义混淆",
            "单位换算错误", "数量关系理解错误",
        ],
        "calculation": [
            "加法进位错", "减借位错", "乘法口诀错", "除法试商错",
            "分数通分错", "计算步骤遗漏",
        ],
        "method": [
            "解题思路错误", "列式错误", "方程设错", "画图误解",
            "推理方向反了", "分类讨论遗漏",
        ],
        "reading": [
            "漏看条件", "审题不清", "理解错题意", "忽略关键信息",
            "答非所问", "问题要求看反了",
        ],
    }
    return random.choice(details.get(err_type, ["未知错误"]))


def main():
    all_homeworks = []
    for week in range(1, 5):
        for hw in range(5):
            homework = generate_homework(week, hw)
            all_homeworks.append(homework)

    output = {
        "meta": {
            "description": "学业量化分析引擎 — 模拟数据集",
            "student_id": STUDENT_ID,
            "subject": SUBJECT,
            "grade": GRADE,
            "weeks": 4,
            "homeworks_per_week": 5,
            "total_homeworks": 20,
            "generated_by": "scripts/generate_mock_data.py",
        },
        "homeworks": all_homeworks,
    }

    out_path = DATA_DIR / "mock_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 摘要
    accuracies = [h["summary"]["accuracy"] for h in all_homeworks]
    weekly_avg = {}
    for h in all_homeworks:
        w = h["week"]
        weekly_avg.setdefault(w, []).append(h["summary"]["accuracy"])

    print(f"✅ 模拟数据已生成: {out_path}")
    print(f"   总作业数: {len(all_homeworks)}")
    print(f"   总体正确率: {sum(accuracies)/len(accuracies):.1%}")
    print(f"   每周平均正确率:")
    for w in sorted(weekly_avg):
        avg = sum(weekly_avg[w]) / len(weekly_avg[w])
        print(f"     第{w}周: {avg:.1%}")
    print(f"   错误类型趋势:")
    for w in sorted(weekly_avg):
        week_hws = [h for h in all_homeworks if h["week"] == w]
        total_err = {e: 0 for e in ERROR_TYPES}
        for hw in week_hws:
            for e, c in hw["summary"]["error_type_distribution"].items():
                total_err[e] += c
        parts = ", ".join(f"{e}: {total_err[e]}" for e in ERROR_TYPES)
        print(f"     第{w}周: {parts}")


if __name__ == "__main__":
    main()