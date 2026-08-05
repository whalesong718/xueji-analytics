# 学迹分析 (Xueji Analytics)

学业量化分析引擎 — 从作业数据中挖掘学习轨迹、掌握度、错误模式，生成可执行的周报和学习计划。

## 项目定位

痛点：学生每天做作业 → 错题只改一次 → 下次还错。缺少对**错误类型**和**掌握度趋势**的系统跟踪。

方案：把每次作业数据化 → 用 BKT 模型追踪每类错误的掌握度 → 规则引擎自动诊断 → 输出周报+学习建议。

---

## 技术栈

- **语言**：Python 3.10+（纯标准库，零第三方依赖）
- **模型**：BKT (Bayesian Knowledge Tracing) 自实现
- **数据**：JSON 格式，dataclass 序列化
- **测试**：模拟数据 20 次作业 × 25 题（4 周 × 5 次）

---

## 项目结构

```
xueji-analytics/
├── engine/                    # 核心引擎
│   ├── data_models.py         # 数据结构定义（输入/输出/中间结果）
│   ├── stats_engine.py        # Phase 1: 统计算法层
│   ├── bkt_engine.py          # Phase 2a: BKT掌握度模型
│   ├── rules_engine.py        # Phase 2b: 规则引擎（8条规则）
│   ├── report_generator.py    # Phase 3: 报告生成层
│   └── __init__.py            # 包入口，导出所有公开接口
├── scripts/
│   ├── run_stats_demo.py      # 全链路验证脚本
│   └── generate_mock_data.py  # 模拟数据生成器
├── data/
│   └── mock_data.json         # 模拟数据集（20次作业）
├── docs/                      # 技术文档
├── tests/                     # 单元测试
├── sample_reports/            # 样例报告输出
└── README.md                  # 本文件
```

## 快速开始

```bash
cd /mnt/e/xueji-analytics
python3 scripts/run_stats_demo.py
```

输出示例：
- 第1周正确率 50% → 第4周 77%
- 掌握度：粗心✅ 概念🔴 计算🔴 方法✅ 审题✅
- 自动生成：错误分析 + 学习建议 + 下周计划