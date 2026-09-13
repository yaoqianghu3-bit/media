"""SQLite 建表与连接管理。

三张表：
  snapshot    一次抓取
  hot_topic   去重后的热榜话题（以知乎问题 id 为身份）
  observation 某话题在某次快照里的那条记录

峰值/当前/增量/变化率一律查询时计算，不落表。
"""
import logging
import sqlite3

from config import DB_PATH

log = logging.getLogger("hotlist")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT    NOT NULL UNIQUE,
    item_count INTEGER NOT NULL DEFAULT 0,
    raw_path   TEXT
);

CREATE TABLE IF NOT EXISTS hot_topic (
    question_id   INTEGER PRIMARY KEY,
    title_latest  TEXT    NOT NULL,
    url           TEXT,
    created_at    INTEGER,
    thumbnail     TEXT,
    first_seen_at TEXT,
    last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS observation (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    INTEGER NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    question_id    INTEGER NOT NULL REFERENCES hot_topic(question_id),
    rank           INTEGER,
    heat_wan       REAL,
    heat_text      TEXT,
    answer_count   INTEGER,
    follower_count INTEGER,
    debut          INTEGER DEFAULT 0,
    card_label     TEXT,
    excerpt        TEXT,
    zhihu_tags     TEXT,
    attached_info  TEXT,
    UNIQUE (snapshot_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_obs_question ON observation (question_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_obs_snapshot ON observation (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_time ON snapshot (fetched_at);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """给已存在的表补列（老库升级用，幂等）。"""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _backfill_web_urls(conn: sqlite3.Connection) -> None:
    """历史数据里 url 存的是 api.zhihu.com 接口地址（浏览器打开是 JSON 报错），
    统一改写成网页地址。"""
    cur = conn.execute(
        "UPDATE hot_topic "
        "SET url = 'https://www.zhihu.com/question/' || question_id "
        "WHERE url LIKE '%api.zhihu.com/questions/%'"
    )
    if cur.rowcount:
        log.info("已把 %s 条历史数据的链接从接口地址改为网页地址", cur.rowcount)


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "snapshot", "raw_path", "raw_path TEXT")
        _backfill_web_urls(conn)
        conn.commit()
    finally:
        conn.close()
