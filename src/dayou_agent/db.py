"""SQLite per FC instance · WAL 模式 · path 走 NAS /mnt/nas/dayou/dayou.sqlite。

v0.1: 1 FC instance + WAL 扛并发 (per-user 操作串行 · 用户间 ok)
v0.2: 拆 RDS · API 不破

per-user 路径隔离:
- 草稿附件 → /mnt/nas/dayou/users/<user_id>/drafts/
- 邮件 cache → 不持久化 (memory only · LLM call 完丢)
"""
from __future__ import annotations
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def _db_path() -> Path:
    """NAS 优先 · 本地 dev fallback ~/.dayou/dayou.sqlite。"""
    nas = os.environ.get("DAYOU_NAS_ROOT", "/mnt/nas/dayou")
    nas_path = Path(nas)
    if nas_path.exists() or os.environ.get("DAYOU_USE_NAS") == "1":
        nas_path.mkdir(parents=True, exist_ok=True)
        return nas_path / "dayou.sqlite"
    # dev fallback
    home = Path.home() / ".dayou"
    home.mkdir(parents=True, exist_ok=True)
    return home / "dayou.sqlite"


_DB_PATH: Path | None = None


def db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = _db_path()
    return _DB_PATH


@contextmanager
def conn():
    c = sqlite3.connect(db_path(), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    finally:
        c.close()


def init():
    """启动调一次 · 幂等。"""
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mailbox_accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            slug            TEXT NOT NULL,
            email           TEXT NOT NULL,
            server_imap     TEXT NOT NULL,
            port_imap       INTEGER NOT NULL,
            server_smtp     TEXT NOT NULL,
            port_smtp       INTEGER NOT NULL,
            app_password_enc TEXT NOT NULL,
            created_at      INTEGER NOT NULL,
            UNIQUE(user_id, slug)
        );
        CREATE INDEX IF NOT EXISTS idx_accounts_user ON mailbox_accounts(user_id);

        CREATE TABLE IF NOT EXISTS drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            account_slug    TEXT NOT NULL,
            to_addr         TEXT NOT NULL,
            cc_addr         TEXT,
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            in_reply_to     TEXT,
            created_at      INTEGER NOT NULL,
            sent_at         INTEGER,
            sent_msg_id     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_drafts_user ON drafts(user_id);

        CREATE TABLE IF NOT EXISTS audit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            action          TEXT NOT NULL,
            slug            TEXT,
            detail          TEXT,
            created_at      INTEGER NOT NULL
        );
        """)


def now_ts() -> int:
    return int(time.time())


def audit(user_id: str, action: str, slug: str | None = None, detail: str | None = None):
    with conn() as c:
        c.execute(
            "INSERT INTO audit_log(user_id, action, slug, detail, created_at) VALUES (?,?,?,?,?)",
            (user_id, action, slug, detail, now_ts()),
        )
