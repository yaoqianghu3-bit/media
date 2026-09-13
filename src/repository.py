"""查询层。

界面 1：最近 N 次快照去重 → 峰值热度（窗口内最大）+ 当前热度（最新一次）+ 本次排名
        按峰值热度降序。最新一次里没有的话题，当前热度与排名都是 None（前端显示 -）。
界面 2：某个话题最近 N 次快照的全部观测行，时间倒序，带 Δ 与变化率。
"""
from __future__ import annotations

import sqlite3

from config import DETAIL_SNAPSHOTS, WINDOW_SNAPSHOTS


def _recent_snapshot_ids(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, fetched_at FROM snapshot ORDER BY fetched_at DESC LIMIT ?", (limit,)
    ).fetchall()


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


def get_status(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, MAX(fetched_at) AS latest FROM snapshot"
    ).fetchone()
    topics = conn.execute("SELECT COUNT(*) AS c FROM hot_topic").fetchone()["c"]
    observations = conn.execute("SELECT COUNT(*) AS c FROM observation").fetchone()["c"]
    total = row["total"] or 0
    return {
        "snapshot_total": total,
        "latest_fetched_at": row["latest"],
        "window_size": WINDOW_SNAPSHOTS,
        "window_filled": min(total, WINDOW_SNAPSHOTS),
        "topic_total": topics,
        "observation_total": observations,
    }


def list_daily_topics(conn: sqlite3.Connection, window: int = WINDOW_SNAPSHOTS) -> dict:
    """界面 1 的数据：窗口内去重后的话题列表。"""
    snaps = _recent_snapshot_ids(conn, window)
    if not snaps:
        return {"latest_fetched_at": None, "window_size": window, "topics": []}

    ids = [r["id"] for r in snaps]
    latest_id = ids[0]
    ph = _placeholders(len(ids))

    # 峰值 = 窗口内 heat_wan 的最大值；当前 = 最新这次快照里的那条
    sql = f"""
        SELECT
            t.question_id,
            t.title_latest                       AS title,
            t.url,
            MAX(o.heat_wan)                      AS peak_heat,
            cur.heat_wan                         AS current_heat,
            cur.heat_text                        AS current_heat_text,
            cur.rank                             AS current_rank
        FROM observation o
        JOIN hot_topic t ON t.question_id = o.question_id
        LEFT JOIN observation cur
               ON cur.question_id = t.question_id
              AND cur.snapshot_id = ?
        WHERE o.snapshot_id IN ({ph})
        GROUP BY t.question_id
        ORDER BY peak_heat IS NULL, peak_heat DESC, t.question_id
    """
    rows = conn.execute(sql, [latest_id, *ids]).fetchall()

    # 知乎问题 id 是 19 位，超过 JS 安全整数上限（2^53），必须当字符串传，
    # 否则浏览器里会被四舍五入成另一个 id，详情接口就查不到了
    topics = []
    for r in rows:
        item = dict(r)
        item["question_id"] = str(item["question_id"])
        topics.append(item)

    return {
        "latest_fetched_at": snaps[0]["fetched_at"],
        "window_size": window,
        "snapshot_used": len(ids),
        "topics": topics,
    }


def get_topic_history(
    conn: sqlite3.Connection, question_id: int, window: int = DETAIL_SNAPSHOTS
) -> dict:
    """界面 2 的数据：该话题在窗口内每一次采集的记录，时间倒序，带增量。"""
    topic = conn.execute(
        "SELECT question_id, title_latest AS title, url, created_at, first_seen_at, last_seen_at "
        "FROM hot_topic WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if topic is None:
        return {"topic": None, "rows": []}

    # 同上：id 一律转字符串
    topic_dict = dict(topic)
    topic_dict["question_id"] = str(topic_dict["question_id"])

    snaps = _recent_snapshot_ids(conn, window)
    if not snaps:
        return {"topic": topic_dict, "rows": []}

    ids = [r["id"] for r in snaps]
    ph = _placeholders(len(ids))

    # LAG 取「时间上更早的那次」，窗口内最早的一行没有前值 → 增量为 None
    sql = f"""
        SELECT
            s.fetched_at,
            o.rank,
            o.heat_wan,
            o.heat_text,
            o.answer_count,
            o.follower_count,
            o.debut,
            o.card_label,
            LAG(o.heat_wan)       OVER w AS prev_heat,
            LAG(o.rank)           OVER w AS prev_rank,
            LAG(o.answer_count)   OVER w AS prev_answer,
            LAG(o.follower_count) OVER w AS prev_follower
        FROM observation o
        JOIN snapshot s ON s.id = o.snapshot_id
        WHERE o.question_id = ? AND s.id IN ({ph})
        WINDOW w AS (ORDER BY s.fetched_at)
        ORDER BY s.fetched_at DESC
    """
    rows = conn.execute(sql, [question_id, *ids]).fetchall()

    out = []
    for r in rows:
        heat, prev_heat = r["heat_wan"], r["prev_heat"]
        delta_heat = None
        heat_pct = None
        if heat is not None and prev_heat not in (None, 0):
            delta_heat = round(heat - prev_heat, 2)
            heat_pct = round((heat - prev_heat) / prev_heat * 100, 1)

        rank, prev_rank = r["rank"], r["prev_rank"]
        # 排名变小 = 上升，所以取 prev - cur，正数表示名次前进
        delta_rank = None if (rank is None or prev_rank is None) else prev_rank - rank

        answer, prev_answer = r["answer_count"], r["prev_answer"]
        delta_answer = None if (answer is None or prev_answer is None) else answer - prev_answer

        follower, prev_follower = r["follower_count"], r["prev_follower"]
        delta_follower = (
            None if (follower is None or prev_follower is None) else follower - prev_follower
        )

        out.append(
            {
                "fetched_at": r["fetched_at"],
                "rank": rank,
                "delta_rank": delta_rank,
                "heat_wan": heat,
                "heat_text": r["heat_text"],
                "delta_heat": delta_heat,
                "heat_pct": heat_pct,
                "answer_count": answer,
                "delta_answer": delta_answer,
                "follower_count": follower,
                "delta_follower": delta_follower,
                "debut": bool(r["debut"]),
                "card_label": r["card_label"],
            }
        )

    return {"topic": topic_dict, "rows": out}
