"""知乎热榜抓取与入库。

接口实测要点（2026-09-13）：
- 一次返回 30 条（服务端偶尔少下发 1 条，不可控，按实际条数入库即可）；
  paging.is_end=true，无分页游标，一次即全量
- trend 恒为 0、comment_count 恒为 0，不可用
- detail_text 是 "474 万热度" 这种中文串，精度到万
- 稳定身份是 target.id；顶层 id 是 "{排名}_{服务器时间戳}"，每次刷新都变
- 老问题会反复上榜，所以必须按 question_id 去重而不是按标题
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config import (
    API_URL,
    MIN_FETCH_INTERVAL_SECONDS,
    RAW_DIR,
    RAW_RETENTION_DAYS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

log = logging.getLogger("hotlist")

# "474 万热度" / "9876 热度"
HEAT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万)?\s*热度")
EXCERPT_LIMIT = 1000

# 接口返回的 target.url 是 API 地址（浏览器打开是一段 JSON 报错），
# 必须换成人类可读的网页地址
WEB_URL_TEMPLATES = {
    "question": "https://www.zhihu.com/question/{id}",
    "article": "https://zhuanlan.zhihu.com/p/{id}",
    "answer": "https://www.zhihu.com/answer/{id}",
}
WEB_URL_FALLBACK = "https://www.zhihu.com/question/{id}"


def parse_heat(text: str | None) -> float | None:
    """把 detail_text 解析成「万」为单位的数值。解析不出返回 None。"""
    if not text:
        return None
    m = HEAT_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    return value if m.group(2) else value / 10000.0


def parse_rank(item_id, fallback: int) -> int:
    """顶层 id 形如 "1_1789231149.295836"，前缀就是排名。"""
    try:
        return int(str(item_id).split("_")[0])
    except (ValueError, AttributeError, IndexError):
        return fallback


def web_url(target: dict, content_id) -> str:
    """把接口的 API 地址换成对应的知乎网页地址。"""
    tpl = WEB_URL_TEMPLATES.get(target.get("type") or "", WEB_URL_FALLBACK)
    return tpl.format(id=content_id)


def fetch_raw(client: httpx.Client | None = None) -> dict:
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.zhihu.com/hot"}
    if client is not None:
        resp = client.get(API_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    with httpx.Client(follow_redirects=True) as tmp:
        resp = tmp.get(API_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()


def normalize(raw: dict) -> tuple[str, list[dict]]:
    """把接口原始 JSON 摊平成待入库的条目列表，返回 (fetched_at, items)。"""
    fetched_at = datetime.now().isoformat(timespec="seconds")
    items: list[dict] = []

    for idx, entry in enumerate(raw.get("data") or [], start=1):
        target = entry.get("target") or {}
        question_id = target.get("id")
        if question_id is None:
            continue

        label = entry.get("card_label") or {}
        children = entry.get("children") or []
        thumbnail = children[0].get("thumbnail") if children else None
        excerpt = target.get("excerpt") or ""

        items.append(
            {
                "question_id": int(question_id),
                "rank": parse_rank(entry.get("id"), idx),
                "heat_wan": parse_heat(entry.get("detail_text")),
                "heat_text": entry.get("detail_text"),
                "answer_count": target.get("answer_count"),
                "follower_count": target.get("follower_count"),
                "debut": 1 if entry.get("debut") else 0,
                "card_label": label.get("type"),
                "excerpt": excerpt[:EXCERPT_LIMIT],
                "zhihu_tags": json.dumps(target.get("bound_topic_ids") or [], ensure_ascii=False),
                "attached_info": entry.get("attached_info"),
                "title": target.get("title") or "(无标题)",
                "url": web_url(target, question_id),
                "created_at": target.get("created"),
                "thumbnail": thumbnail,
            }
        )

    return fetched_at, items


def raw_file_path(fetched_at: str) -> Path:
    """按请求时间生成留档路径：data/raw/YYYY-MM/YYYYMMDD-HHMMSS.json.gz"""
    dt = datetime.fromisoformat(fetched_at)
    return RAW_DIR / dt.strftime("%Y-%m") / f"{dt.strftime('%Y%m%d-%H%M%S')}.json.gz"


def save_raw(raw: dict, fetched_at: str) -> str:
    """把接口原始 JSON 无损落盘（gzip）。同秒重复抓取自动改名，不覆盖。"""
    path = raw_file_path(fetched_at)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():  # 同一秒内抓了两次，加后缀避免覆盖
        stem, n = path.stem, 2
        while path.exists():
            path = path.with_name(f"{stem}-{n}.json.gz")
            n += 1

    payload = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(payload)

    if RAW_RETENTION_DAYS:
        prune_raw(RAW_RETENTION_DAYS)

    return str(path)


def prune_raw(retention_days: int) -> int:
    """删掉超过保留期的原始文件，返回删除数量。"""
    if not RAW_DIR.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for path in RAW_DIR.rglob("*.json.gz"):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            path.unlink()
            removed += 1
    if removed:
        log.info("原始留档轮转：删除 %s 个超过 %s 天的文件", removed, retention_days)
    return removed


def fetch_and_save(conn, force: bool = False) -> tuple[int | None, int, str]:
    """抓一次并入库。返回 (snapshot_id, 条数, 状态)。

    状态可能是 ok / skipped / empty。同一次快照重复执行不会产生重复行。
    """
    last = conn.execute(
        "SELECT fetched_at FROM snapshot ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()

    if last and not force:
        prev = datetime.fromisoformat(last["fetched_at"])
        if (datetime.now() - prev).total_seconds() < MIN_FETCH_INTERVAL_SECONDS:
            return None, 0, "skipped"

    raw = fetch_raw()
    fetched_at, items = normalize(raw)
    # 原始档紧跟解析落盘：哪怕后面入库失败，这次请求的证据也已经留住了
    raw_path = save_raw(raw, fetched_at)
    if not items:
        return None, 0, "empty"

    cur = conn.execute(
        "INSERT INTO snapshot (fetched_at, item_count, raw_path) VALUES (?, ?, ?)",
        (fetched_at, len(items), raw_path),
    )
    snapshot_id = cur.lastrowid

    for it in items:
        conn.execute(
            """
            INSERT INTO hot_topic
                (question_id, title_latest, url, created_at, thumbnail, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                title_latest  = excluded.title_latest,
                url           = excluded.url,
                thumbnail     = excluded.thumbnail,
                last_seen_at  = excluded.last_seen_at,
                first_seen_at = COALESCE(hot_topic.first_seen_at, excluded.first_seen_at)
            """,
            (
                it["question_id"],
                it["title"],
                it["url"],
                it["created_at"],
                it["thumbnail"],
                fetched_at,
                fetched_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO observation
                (snapshot_id, question_id, rank, heat_wan, heat_text, answer_count,
                 follower_count, debut, card_label, excerpt, zhihu_tags, attached_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id, question_id) DO UPDATE SET
                rank           = excluded.rank,
                heat_wan       = excluded.heat_wan,
                heat_text      = excluded.heat_text,
                answer_count   = excluded.answer_count,
                follower_count = excluded.follower_count,
                debut          = excluded.debut,
                card_label     = excluded.card_label,
                excerpt        = excluded.excerpt,
                zhihu_tags     = excluded.zhihu_tags,
                attached_info  = excluded.attached_info
            """,
            (
                snapshot_id,
                it["question_id"],
                it["rank"],
                it["heat_wan"],
                it["heat_text"],
                it["answer_count"],
                it["follower_count"],
                it["debut"],
                it["card_label"],
                it["excerpt"],
                it["zhihu_tags"],
                it["attached_info"],
            ),
        )

    conn.commit()
    log.info("入库完成 snapshot=%s 条数=%s", snapshot_id, len(items))
    return snapshot_id, len(items), "ok"
