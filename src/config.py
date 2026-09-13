"""全局配置。改这里就够了。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "hotlist.db"

# 接口原始 JSON 全量留档：每次快照存一个文件，路径 data/raw/YYYY-MM/YYYYMMDD-HHMMSS.json.gz
# gzip 是无损压缩，解压后与接口返回的字节完全一致
RAW_DIR = DATA_DIR / "raw"
# None = 永久保留（默认）。想轮转就填天数，例如 30 表示只留最近 30 天
RAW_RETENTION_DAYS = None

# 知乎热榜接口（匿名可访问，无需 cookie）
API_URL = "https://api.zhihu.com/topstory/hot-list"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20.0

# 抓取节奏
FETCH_INTERVAL_MINUTES = 60
# 距上次抓取不足这个秒数就跳过，避免重复入库（手动触发也受此保护）
MIN_FETCH_INTERVAL_SECONDS = 10 * 60

# 界面 1：最近 N 次快照去重（用户要求 24 次 ≈ 一日）
WINDOW_SNAPSHOTS = 24
# 界面 2：话题详情看最近 N 次快照（用户要求 3 日 ≈ 72 次）
DETAIL_SNAPSHOTS = 72

HOST = "127.0.0.1"
PORT = 8000
