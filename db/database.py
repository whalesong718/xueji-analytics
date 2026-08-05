"""SQLite 连接与建表。

- DB 文件位于项目根 data/xueji.db（启动时自动创建，data/ 目录已存在）。
- get_db() 为上下文管理器，自动 commit/rollback；row_factory 用 sqlite3.Row。
- 表结构见 init_db()。外键级联删除需 PRAGMA foreign_keys=ON（每个连接单独开启）。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 项目根 = db/ 的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "xueji.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS homeworks (
    homework_id     TEXT PRIMARY KEY,
    student_id      TEXT NOT NULL,
    date            TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT 'math',
    grade           INTEGER NOT NULL DEFAULT 0,
    total_questions INTEGER NOT NULL,
    correct_count   INTEGER NOT NULL,
    wrong_count     INTEGER NOT NULL,
    blank_count     INTEGER NOT NULL,
    week            INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homeworks_student
    ON homeworks (student_id, subject, date);

CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    homework_id   TEXT NOT NULL,
    q_num         INTEGER NOT NULL,
    type          TEXT NOT NULL DEFAULT 'calculation',
    correct       INTEGER,          -- 1/0/NULL(空题)
    error_type    TEXT,
    error_detail  TEXT,
    difficulty    TEXT NOT NULL DEFAULT 'medium',
    confidence    REAL NOT NULL DEFAULT 1.0,
    source        TEXT NOT NULL DEFAULT 'parent_confirmed',
    question_content TEXT,
    FOREIGN KEY (homework_id) REFERENCES homeworks(homework_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_questions_hw ON questions (homework_id);

CREATE TABLE IF NOT EXISTS reports (
    report_id    TEXT PRIMARY KEY,
    student_id   TEXT NOT NULL,
    subject      TEXT NOT NULL,
    week         INTEGER,
    date_range   TEXT,
    report_text  TEXT,
    report_json  TEXT,
    generated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_student
    ON reports (student_id, subject, generated_at DESC);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """增量迁移：为旧库补列（已存在的列跳过）。

    每次加字段都在这里追加一条 ALTER，靠 PRAGMA table_info 探测是否已有。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)").fetchall()}
    if "question_content" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN question_content TEXT")


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """获取数据库连接（上下文管理器）。

    - 自动开启外键约束（级联删除依赖）。
    - 正常退出 commit，异常 rollback。
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """建表（幂等）+ 增量迁移。在 api/main.py 启动事件里调用。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
