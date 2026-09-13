"""HTTP 接口 + 页面托管。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from config import DETAIL_SNAPSHOTS, WEB_DIR, WINDOW_SNAPSHOTS
from db import get_conn
from fetcher import fetch_and_save
from repository import get_status, get_topic_history, list_daily_topics

log = logging.getLogger("hotlist")


def create_app() -> FastAPI:
    app = FastAPI(title="知乎热榜选题面板", version="0.1.0")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/status")
    def status():
        conn = get_conn()
        try:
            return get_status(conn)
        finally:
            conn.close()

    @app.get("/api/snapshots")
    def snapshots(limit: int = Query(48, ge=1, le=2000)):
        """列出最近的快照，含原始留档路径与文件体积。"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, fetched_at, item_count, raw_path "
                "FROM snapshot ORDER BY fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()

        out = []
        for r in rows:
            size = None
            if r["raw_path"] and Path(r["raw_path"]).exists():
                size = Path(r["raw_path"]).stat().st_size
            out.append(
                {
                    "id": r["id"],
                    "fetched_at": r["fetched_at"],
                    "item_count": r["item_count"],
                    "raw_path": r["raw_path"],
                    "raw_bytes": size,
                }
            )
        return {"snapshots": out}

    @app.get("/api/snapshots/{snapshot_id}/raw")
    def snapshot_raw(snapshot_id: int):
        """下载某次快照的接口原始 JSON（gzip）。"""
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT fetched_at, raw_path FROM snapshot WHERE id = ?", (snapshot_id,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise HTTPException(status_code=404, detail="没有这次快照")
        if not row["raw_path"] or not Path(row["raw_path"]).exists():
            raise HTTPException(status_code=404, detail="这次快照没有留下原始文件")

        path = Path(row["raw_path"])
        return FileResponse(
            path,
            media_type="application/gzip",
            filename=f"{row['fetched_at'].replace(':', '').replace('-', '')}.json.gz",
        )

    @app.get("/api/topics")
    def topics(window: int = Query(WINDOW_SNAPSHOTS, ge=1, le=720)):
        conn = get_conn()
        try:
            return list_daily_topics(conn, window)
        finally:
            conn.close()

    @app.get("/api/topics/{question_id}")
    def topic_detail(
        question_id: int,
        window: int = Query(DETAIL_SNAPSHOTS, ge=1, le=720),
    ):
        conn = get_conn()
        try:
            data = get_topic_history(conn, question_id, window)
        finally:
            conn.close()
        if data["topic"] is None:
            raise HTTPException(status_code=404, detail="未采集到该话题")
        return data

    @app.post("/api/fetch")
    def do_fetch(force: bool = False):
        """手动触发一次抓取。默认受最短间隔保护，force=true 可绕过。"""
        conn = get_conn()
        try:
            snapshot_id, count, state = fetch_and_save(conn, force=force)
        except Exception as exc:  # noqa: BLE001 - 接口层统一转成 500
            log.exception("手动抓取失败")
            raise HTTPException(status_code=500, detail=f"抓取失败：{exc}") from exc
        finally:
            conn.close()
        return {"snapshot_id": snapshot_id, "count": count, "state": state}

    return app
