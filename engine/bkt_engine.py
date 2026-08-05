"""
学业量化分析引擎 — BKT 掌握度模型（按错误类型追踪）。

BKT 四参数含义（映射到学业场景）：
  L0: 初始掌握度 — 该类型错误一开始不会犯的概率
  T:  学习率 — 每次作业后改善的概率
  G:  猜对率 — 没掌握但做对的概率（选择题偏高）
  S:  失误率 — 掌握了但做错的概率（粗心偏高）

核心公式：
  做对（该类型没错）: L_new = L * (1-S) / (L*(1-S) + (1-L)*G)
  做错（该类型出错）: L_new = L * S / (L*S + (1-L)*(1-G))
  无数据间隔:        L_new = L * decay_factor(间隔天数)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from engine.data_models import ErrorType, Homework


# ---------------------------------------------------------------------------
# BKT 参数表（按错误类型差异化设定）
# ---------------------------------------------------------------------------

# (L0, T, G, S)
BKT_PARAMS = {
    ErrorType.CALCULATION.value: (0.6, 0.3, 0.1, 0.15),   # 计算失误：S偏高（会但算错）
    ErrorType.CONCEPT.value:     (0.4, 0.2, 0.15, 0.05),   # 概念不清：S低（不会就是不会）
    ErrorType.METHOD.value:      (0.3, 0.25, 0.1, 0.05),   # 方法错误：没掌握，S低
    ErrorType.READING.value:     (0.5, 0.2, 0.3, 0.1),     # 审题错误：猜对率高
    ErrorType.CARELESS.value:    (0.7, 0.15, 0.05, 0.2),   # 粗心/习惯：S最高
}

# 遗忘衰减系数
DECAY_PER_DAY = 0.95  # 每天衰减5%
DECAY_FLOOR = 0.3     # 最低衰减到的掌握度


@dataclass
class BKTState:
    """单个错误类型的 BKT 状态。"""
    error_type: str
    mastery: float          # 当前掌握度 (0-1)
    L0: float               # 初始掌握度
    T: float                # 学习率
    G: float                # 猜对率
    S: float                # 失误率
    observation_count: int = 0          # 该类错误被观察的次数
    last_update_date: Optional[str] = None  # 上次更新日期 YYYY-MM-DD
    mastery_history: list[dict] = field(default_factory=list)  # 历史记录

    def to_dict(self) -> dict:
        return {
            "error_type": self.error_type,
            "mastery": round(self.mastery, 4),
            "L0": self.L0,
            "T": self.T,
            "G": self.G,
            "S": self.S,
            "observation_count": self.observation_count,
            "last_update_date": self.last_update_date,
            "mastery_history": self.mastery_history[-20:],  # 保留最近20条
        }


@dataclass
class BKTEngine:
    """BKT 引擎，管理所有错误类型的掌握度状态。"""
    states: dict[str, BKTState] = field(default_factory=dict)

    def get_or_create(self, error_type: str) -> BKTState:
        if error_type not in self.states:
            params = BKT_PARAMS.get(error_type, (0.5, 0.3, 0.2, 0.1))
            self.states[error_type] = BKTState(
                error_type=error_type,
                mastery=params[0],
                L0=params[0],
                T=params[1],
                G=params[2],
                S=params[3],
            )
        return self.states[error_type]

    def update(
        self,
        error_type: str,
        had_error: bool,
        date: str,
        error_count: int = 1,
    ) -> float:
        """更新某个错误类型的掌握度。

        Args:
            error_type: 错误类型名称。
            had_error: 本次作业是否犯了该类错误。
            date: 作业日期 YYYY-MM-DD。
            error_count: 该类错误出现的次数（影响更新权值）。

        Returns:
            更新后的掌握度。
        """
        state = self.get_or_create(error_type)

        # 1. 先应用遗忘衰减
        if state.last_update_date and date > state.last_update_date:
            days_gap = _date_diff(state.last_update_date, date)
            if days_gap >= 2:  # 超过2天无数据才衰减
                decay = DECAY_PER_DAY ** days_gap
                state.mastery = max(
                    state.mastery * decay,
                    DECAY_FLOOR
                )

        # 2. BKT 更新
        L = state.mastery
        G = state.G
        S = state.S

        if had_error:
            # 做错了 → 掌握度下降
            numerator = L * S
            denominator = L * S + (1 - L) * (1 - G)
        else:
            # 没犯错 → 掌握度提升
            numerator = L * (1 - S)
            denominator = L * (1 - S) + (1 - L) * G

        if denominator > 0:
            L_new = numerator / denominator
        else:
            L_new = L

        # 3. 学习效果：T 参数，多次犯错影响学习
        # 每次作业后，有 T 的概率改善（即使这次做错了，下次也可能做对）
        if had_error:
            # 犯错后，掌握度 = 当前掌握度 + T * (1 - 当前掌握度) 的部分
            # 但犯错本身已经是负面信号，这里用加权
            L_new = L_new + state.T * (1 - L_new) * 0.3  # 犯错后学习效果打折扣
        else:
            # 没有犯错，T 自然生效
            L_new = L_new + state.T * (1 - L_new) * 0.5

        # 4. 夹紧到 [0, 1]
        L_new = max(0.01, min(0.99, L_new))

        state.mastery = round(L_new, 4)
        state.observation_count += error_count
        state.last_update_date = date

        # 记录历史
        state.mastery_history.append({
            "date": date,
            "mastery": round(L_new, 4),
            "had_error": had_error,
            "error_count": error_count,
        })

        return state.mastery

    def update_from_homework(self, hw: Homework):
        """根据一次作业更新所有错误类型的掌握度。"""
        date = hw.date

        # 统计本次作业中各类错误的出现次数
        error_counts: dict[str, int] = {e.value: 0 for e in ErrorType}
        for q in hw.questions:
            if q.correct is False and q.error_type:
                if q.error_type in error_counts:
                    error_counts[q.error_type] += 1
                else:
                    error_counts[q.error_type] = error_counts.get(q.error_type, 0) + 1

        # 更新每类错误
        for e in ErrorType:
            count = error_counts.get(e.value, 0)
            self.update(
                error_type=e.value,
                had_error=count > 0,
                date=date,
                error_count=count,
            )

    def get_mastery(self, error_type: str) -> float:
        """获取当前掌握度。"""
        state = self.get_or_create(error_type)
        return state.mastery

    def get_all_masteries(self) -> dict[str, float]:
        """获取所有错误类型的当前掌握度。"""
        return {e.value: self.get_mastery(e.value) for e in ErrorType}

    def to_dict(self) -> dict:
        return {
            error_type: state.to_dict()
            for error_type, state in self.states.items()
        }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _date_diff(date1: str, date2: str) -> int:
    """计算两个日期之间的天数差。"""
    d1 = datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.strptime(date2, "%Y-%m-%d")
    return abs((d2 - d1).days)


def run_bkt_sequence(
    homeworks: list[Homework],
) -> BKTEngine:
    """按时间顺序对一组作业运行 BKT 更新。

    Args:
        homeworks: 按时间排序的 Homework 列表。

    Returns:
        更新后的 BKTEngine。
    """
    engine = BKTEngine()
    for hw in homeworks:
        engine.update_from_homework(hw)
    return engine


def summarize_bkt(engine: BKTEngine) -> dict:
    """生成 BKT 掌握度摘要。"""
    summary = {}
    for e in ErrorType:
        state = engine.get_or_create(e.value)
        mastery = state.mastery
        # 掌握度分级
        if mastery >= 0.8:
            level = "已掌握"
        elif mastery >= 0.6:
            level = "基本掌握"
        elif mastery >= 0.4:
            level = "需要关注"
        else:
            level = "薄弱"

        summary[e.value] = {
            "mastery": round(mastery, 4),
            "level": level,
            "observations": state.observation_count,
            "last_update": state.last_update_date,
        }
    return summary