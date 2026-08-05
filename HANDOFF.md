# 学迹分析 · 交接文档

> 最后更新：2026-08-05 22:00
> 交接人：WSL side (mirage)
> 接手方：Windows 端 agent

---

## 一、项目概览

**项目名**：学迹分析 (Xueji Analytics)
**用途**：从学生作业数据中分析学习轨迹、掌握度、错误模式，生成周报和计划。
**仓库位置**：`E:\xueji-analytics`（WSL 里是 `/mnt/e/xueji-analytics`）

---

## 二、当前状态

### ✅ 已完成（核心引擎全部就绪）

| 阶段 | 内容 | 文件 |
|------|------|------|
| Phase 1 | 统计算法层（正确率/完成率/趋势/错误模式） | `engine/stats_engine.py` |
| Phase 2a | BKT 掌握度模型（5类错误×遗忘衰减×学习率衰减） | `engine/bkt_engine.py` |
| Phase 2b | 规则引擎（8条规则 R001-R008） | `engine/rules_engine.py` |
| Phase 3 | 报告生成层（6章周报：概览/掌握度/错误/规则/建议/计划） | `engine/report_generator.py` |
| 数据 | 模拟数据集（20次作业×4周×25题） | `data/mock_data.json` |
| 验证 | 全链路验证脚本通过 | `scripts/run_stats_demo.py` |

### 🔄 完成但待修正

- **FastAPI 后端**（`api/main.py`）：已搭建，路由 `api/v1/analyze/demo` 可用。但 WSL 启动后 `/health` 返回 404（原因：`/health` 路由在 `api/main.py` 第41行，和 `StaticFiles` 挂载顺序冲突——静态文件通配 `/*` 拦截了 `/health`）。需要将 `health` 路由移到 `StaticFiles` 挂载之前，或加 `name` 参数避免冲突。

### ❌ 未开始

- 数据库接入（当前所有路由标记 `TODO: 持久化存储`）
- 小程序前端（`miniapp/` 目录有 `app.json` 骨架，无实际页面）
- 单元测试
- 部署上线

---

## 三、如何接手

### 3.1 环境准备

```powershell
# Windows 端
cd E:\xueji-analytics

# 检查 Python 版本 ≥ 3.10
python --version

# 安装依赖
pip install fastapi uvicorn

# 验证核心引擎
python scripts/run_stats_demo.py
```

### 3.2 启动 API 服务

```powershell
cd E:\xueji-analytics
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000/api/v1/analyze/demo` 验证全链路分析。

### 3.3 验证点

- 正确输出：第1周正确率 ~50% → 第4周 ~77%
- BKT 掌握度显示 5 类错误各自的掌握度等级
- 规则引擎触发 R001（持续进步）等规则

---

## 四、架构速览

### 数据流

```
Homework JSON → stats_engine (统计) 
              → bkt_engine (BKT掌握度) 
              → rules_engine (规则诊断) 
              → report_generator (报告生成)
              → WeeklyReport (输出)
```

### 核心数据结构

```
Homework (输入)
  ├── student_id, date, subject, grade
  ├── total_questions, correct_count, wrong_count, blank_count
  └── questions: list[QuestionResult]
        ├── q_num, type, correct, error_type
        └── difficulty, source

WeeklyReport (输出)
  ├── 6 sections: 概览/掌握度/错误分析/规则结论/建议/计划
  └── feedback: 家长反馈选项
```

### 5类错误（ErrorType）

| 枚举值 | 中文 | 说明 |
|--------|------|------|
| careless | 粗心/习惯 | 抄错数、漏符号 |
| concept | 概念不清 | 公式记错、定理理解 |
| calculation | 计算失误 | 进位错、算错数 |
| method | 方法错误 | 解题方向错 |
| reading | 审题错误 | 看错条件、答非所问 |

### 8条规则

| 编号 | 名称 | 方向 |
|------|------|------|
| R001 | 持续进步 | 正面 |
| R002 | 反复犯错 | 负面 |
| R003 | 问题已解决 | 正面 |
| R004 | 新出现问题 | 负面 |
| R005 | 难题放弃 | 负面 |
| R006 | 进步停滞 | 中性 |
| R007 | 计算改善 | 正面 |
| R008 | 退步预警 | 负面 |

---

## 五、WSL → Windows 切换注意事项

### 5.1 已知问题

1. `/health` 返回 404：路由顺序问题，需要将 `StaticFiles` 挂载放到 `health` 路由之后（见上文）
2. 模拟数据路径：`data/mock_data.json` 是相对路径，Windows 下路径分隔符自动兼容
3. 纯标准库，零第三方依赖（除 FastAPI + Uvicorn）

### 5.2 建议优先级

1. **修好 `/health` 路由** — 第一步
2. **接入真实数据** — 定义录入格式，先跑起来
3. **HTML 前端** — 当前 `frontend/index.html` 是单页纯前端，调 API 展示报告
4. **持久化存储** — 当前所有路由都是内存/空实现

---

## 六、相关文档

- `docs/技术文档.md` — 完整技术细节（BKT 参数、规则阈值、输出格式）
- `docs/状态与路线图.md` — 路线图、待办、技术债
- `docs/小程序架构问题清单.md` — 小程序架构相关
- `sample_reports/第4周报告.txt` — 样例报告输出

---

## 七、快速验证命令

```powershell
# 1. 跑全链路验证
cd E:\xueji-analytics
python scripts/run_stats_demo.py

# 2. 启动 API
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 3. 测试 demo 接口
curl http://localhost:8000/api/v1/analyze/demo

# 4. 测试健康检查（修好后）
curl http://localhost:8000/health
```

祝你接手顺利 🚀