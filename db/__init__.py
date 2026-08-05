"""学迹分析 · 持久层（SQLite）。

对外只暴露 repository 的 CRUD 函数，调用方不直接接触 sqlite3。
"""

from db.database import get_db, init_db, DB_PATH  # noqa: F401
from db import repository  # noqa: F401
