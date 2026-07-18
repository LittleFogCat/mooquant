# -*- coding: utf-8 -*-
"""
mookquant 路 SQLite 鍘嗗彶K绾跨紦瀛樻ā鍧?

职责：
  1. 管理本地 SQLite 数据库（data/mooquant.db）
  2. 提供K线数据的缓存查询 / 写入 / 增量更新接口
  3. 供 qmt_server.py 和 backtest_engine.py 共享使用

设计：
  - 表 kline_history 按 (code, period, date, dividend_type) 唯一
  - 使用 WAL 模式支持多进程并发读写
  - INSERT OR IGNORE 避免重复插入
"""
import os
import sqlite3
import threading

# 数据库文件路径：项目根目录 / data / mooquant.db
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "mooquant.db")

# 线程锁，保证同进程内建表操作的线程安全
_init_lock = threading.Lock()
_initialized = False


def _get_conn():
    """获取数据库连接（WAL 模式，支持多进程并发）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """初始化数据库表（幂等，多次调用安全）"""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kline_history (
                    code          TEXT NOT NULL,
                    period        TEXT NOT NULL,
                    date          TEXT NOT NULL,
                    open          REAL NOT NULL,
                    high          REAL NOT NULL,
                    low           REAL NOT NULL,
                    close         REAL NOT NULL,
                    volume        REAL NOT NULL DEFAULT 0,
                    amount        REAL NOT NULL DEFAULT 0,
                    dividend_type TEXT NOT NULL DEFAULT 'front',
                    PRIMARY KEY (code, period, date, dividend_type)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kline_lookup
                ON kline_history (code, period, dividend_type, date)
            """)
            conn.commit()
            init_stocks_table()
            _initialized = True
        finally:
            conn.close()


def query_bars(code, period, count=-1, dividend_type="front"):
    """从数据库查询K线数据（按日期升序返回）

    Args:
        code:          xtquant 标准代码，如 "600036.SH"
        period:        周期，如 "1d" / "1w" / "1h"
        count:         取最近 N 条；<=0 表示取全部
        dividend_type: 复权类型

    Returns:
        list[dict]  每条含 date/open/high/low/close/volume/amount
    """
    init_db()
    conn = _get_conn()
    try:
        if count > 0:
            rows = conn.execute(
                "SELECT * FROM kline_history "
                "WHERE code=? AND period=? AND dividend_type=? "
                "ORDER BY date DESC LIMIT ?",
                (code, period, dividend_type, count),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = conn.execute(
                "SELECT * FROM kline_history "
                "WHERE code=? AND period=? AND dividend_type=? "
                "ORDER BY date ASC",
                (code, period, dividend_type),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_bars_by_date(code, period, start_date, end_date, dividend_type="front"):
    """按日期范围查询K线数据（升序）

    Args:
        start_date / end_date: "YYYY-MM-DD" 格式
    """
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM kline_history "
            "WHERE code=? AND period=? AND dividend_type=? "
            "AND date>=? AND date<=? ORDER BY date ASC",
            (code, period, dividend_type, start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_bars(code, period, bars, dividend_type="front"):
    """保存K线数据到数据库（已存在的自动跳过）

    Returns:
        int  实际新增的行数
    """
    if not bars:
        return 0
    init_db()
    conn = _get_conn()
    try:
        data = [
            (code, period, bar["date"], bar["open"], bar["high"],
             bar["low"], bar["close"], bar.get("volume", 0),
             bar.get("amount", 0), dividend_type)
            for bar in bars
        ]
        before = conn.execute(
            "SELECT COUNT(*) FROM kline_history "
            "WHERE code=? AND period=? AND dividend_type=?",
            (code, period, dividend_type),
        ).fetchone()[0]

        conn.executemany(
            "INSERT OR IGNORE INTO kline_history "
            "(code, period, date, open, high, low, close, volume, amount, dividend_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            data,
        )
        conn.commit()

        after = conn.execute(
            "SELECT COUNT(*) FROM kline_history "
            "WHERE code=? AND period=? AND dividend_type=?",
            (code, period, dividend_type),
        ).fetchone()[0]
        return after - before
    finally:
        conn.close()


def get_latest_date(code, period, dividend_type="front"):
    """获取数据库中某股票某周期的最新日期（YYYY-MM-DD 或 None）"""
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(date) AS latest FROM kline_history "
            "WHERE code=? AND period=? AND dividend_type=?",
            (code, period, dividend_type),
        ).fetchone()
        return row["latest"] if row else None
    finally:
        conn.close()


def get_count(code, period, dividend_type="front"):
    """获取数据库中某股票某周期的数据条数"""
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kline_history "
            "WHERE code=? AND period=? AND dividend_type=?",
            (code, period, dividend_type),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()

# ----------------------------------------------------------------------
# stocks 表：A 股股票列表缓存
# ----------------------------------------------------------------------

def init_stocks_table():
    """创建 stocks 表（幂等），含 type 字段"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                code        TEXT PRIMARY KEY,
                xtcode      TEXT NOT NULL,
                name        TEXT NOT NULL,
                exchange    TEXT NOT NULL,
                type        TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL
            )
        """)
        # 迁移：旧表可能没有 type 列
        try:
            conn.execute("ALTER TABLE stocks ADD COLUMN type TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # 列已存在
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stocks_name ON stocks(name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stocks_type ON stocks(type)"
        )
        conn.commit()
    finally:
        conn.close()


def save_stocks(stocks):
    """批量写入股票列表（INSERT OR REPLACE）

    Args:
        stocks: list[dict]  每条含 code/xtcode/name/exchange

    Returns:
        int  写入条数
    """
    if not stocks:
        return 0
    init_stocks_table()
    from datetime import datetime
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        data = [
            (s["code"], s["xtcode"], s["name"], s.get("exchange", ""), s.get("type", ""), now)
            for s in stocks
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO stocks "
            "(code, xtcode, name, exchange, type, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            data,
        )
        conn.commit()
        return len(data)
    finally:
        conn.close()


def get_all_stocks():
    """返回全部股票列表（按 code 排序）"""
    init_stocks_table()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT code, name, exchange, type FROM stocks ORDER BY code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stock_count():
    """返回 stocks 表中的股票数量"""
    init_stocks_table()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM stocks").fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()
