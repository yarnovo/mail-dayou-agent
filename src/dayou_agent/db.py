"""dayou-specific schema (mailbox_accounts + drafts) · 通用部分用 akong-agent-base.db。

通用 chat_messages / chat_summaries / audit_log → lib 自动建。
本文件只管 dayou 自己的两表 · server.py 启动调 init() 一次。

env (设在 deploy.yml · GHA 注入到 FC):
- AGENT_NAS_ROOT=/mnt/nas/dayou  · 跟现 prod NAS 路径一致
- AGENT_DB_NAME=dayou.sqlite     · 跟现 prod sqlite 文件一致
"""
from __future__ import annotations

# re-export lib API 让旧 import (db.conn / db.now_ts / db.audit) 不破
from akong_agent_base.db import conn, now_ts, audit, init as _lib_init, exec_script  # noqa: F401


DAYOU_SCHEMA = """
-- dayou-specific: 用户挂的 IMAP/SMTP 凭证 (Fernet 加密)
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

-- dayou-specific: 待发草稿
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
"""


def init() -> None:
    """启动一次 · 幂等。先建通用表 (lib) · 再加 dayou 表。"""
    _lib_init()
    exec_script(DAYOU_SCHEMA)
