"""入口：建库 → 立即抓一次 → 起每小时定时器 → 起 Web 服务。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 让 `python src/main.py` 和 `python -m src.main` 两种启动方式都能 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402
from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402

from api import create_app  # noqa: E402
from config import FETCH_INTERVAL_MINUTES, HOST, PORT
from db import get_conn, init_db
from fetcher import fetch_and_save

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("hotlist")


def fetch_job() -> None:
    conn = get_conn()
    try:
        snapshot_id, count, state = fetch_and_save(conn)
        if state == "ok":
            log.info("定时抓取完成：第 %s 次快照，%s 条", snapshot_id, count)
        elif state == "skipped":
            log.info("距上次抓取过近，跳过")
        else:
            log.warning("本次抓取没有拿到数据")
    except Exception:
        log.exception("定时抓取失败")
    finally:
        conn.close()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - 老版本或非 TTY 下没有该方法
            pass

    init_db()
    log.info("数据库就绪")

    fetch_job()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        fetch_job,
        trigger="interval",
        minutes=FETCH_INTERVAL_MINUTES,
        id="hotlist-fetch",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "已启动定时抓取，每 %s 分钟一次，下次约 %s",
        FETCH_INTERVAL_MINUTES,
        (datetime.now() + timedelta(minutes=FETCH_INTERVAL_MINUTES)).strftime("%H:%M"),
    )

    app = create_app()
    log.info("面板地址 http://%s:%s", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
