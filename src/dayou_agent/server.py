"""阿空大邮 (mail-dayou) FastAPI · multi-user IMAP/SMTP 邮箱管理。

多用户隔离:
- 每 request 必带 X-User-ID header
- middleware 拒绝无 user_id 请求 (401)
- 凭证 Fernet 加密 (per-user derived key) 存 sqlite
- per-user 文件路径 /mnt/nas/dayou/users/<user_id>/

API:
- GET  /health
- GET  /api/providers/guess?email=you@gmail.com
- GET  /api/providers/list
- POST /api/mailbox/connect
- GET  /api/mailbox/accounts
- POST /api/mailbox/list
- POST /api/mailbox/read
- POST /api/mailbox/draft
- POST /api/mailbox/send  (强制 user_confirmed=true)
- POST /api/mailbox/archive
- DELETE /api/mailbox/account
"""
from __future__ import annotations
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from . import db, imap_client, smtp_client
from .crypto import encrypt, decrypt
from .providers import guess_provider, PROVIDERS
from .llm_chat import chat_turn


app = FastAPI(title="mail-dayou-agent", version="0.1.0",
              description="阿空大邮 · multi-user 邮箱管理 (IMAP + SMTP)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://chat.dayou.mail.agentaily.com",
        "https://m.mail.agentaily.com",
        "https://staging.chat.dayou.mail.agentaily.com",
        "https://staging.m.mail.agentaily.com",
        "http://localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init()


def require_user(x_user_id: str | None) -> str:
    if not x_user_id or len(x_user_id) < 8:
        raise HTTPException(401, "X-User-ID header missing or invalid (≥8 chars)")
    return x_user_id


@app.get("/health")
def health():
    return {"status": "ok", "agent": "dayou", "version": "0.1.0", "multi_user": True}


@app.get("/api/providers/guess")
def providers_guess(email: str):
    p = guess_provider(email)
    if not p:
        return {"matched": False,
                "domain": email.split("@")[-1] if "@" in email else "",
                "hint": "服务商不在内置表 · 请手动填 server / port"}
    return {
        "matched": True, "name": p.name,
        "imap_host": p.imap_host, "imap_port": p.imap_port,
        "smtp_host": p.smtp_host, "smtp_port": p.smtp_port,
        "app_password_help": p.app_password_help,
        "app_password_url": p.app_password_url,
    }


@app.get("/api/providers/list")
def providers_list():
    return [
        {"domain": d, "name": p.name, "imap_host": p.imap_host, "smtp_host": p.smtp_host}
        for d, p in PROVIDERS.items()
    ]


class ConnectReq(BaseModel):
    slug: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    server_imap: str
    port_imap: int = 993
    server_smtp: str
    port_smtp: int = 465
    app_password: str = Field(..., min_length=4, max_length=128)


@app.post("/api/mailbox/connect")
def mailbox_connect(req: ConnectReq, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    ok, msg = imap_client.verify_credentials(
        req.server_imap, req.port_imap, str(req.email), req.app_password
    )
    if not ok:
        raise HTTPException(400, msg)
    enc = encrypt(user_id, req.app_password)
    with db.conn() as c:
        c.execute(
            """INSERT INTO mailbox_accounts
            (user_id, slug, email, server_imap, port_imap, server_smtp, port_smtp, app_password_enc, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, slug) DO UPDATE SET
              email=excluded.email,
              server_imap=excluded.server_imap, port_imap=excluded.port_imap,
              server_smtp=excluded.server_smtp, port_smtp=excluded.port_smtp,
              app_password_enc=excluded.app_password_enc""",
            (user_id, req.slug, str(req.email), req.server_imap, req.port_imap,
             req.server_smtp, req.port_smtp, enc, db.now_ts()),
        )
    db.audit(user_id, "connect", req.slug, str(req.email))
    return {"ok": True, "slug": req.slug, "email": req.email}


@app.get("/api/mailbox/accounts")
def mailbox_accounts(x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    with db.conn() as c:
        rows = c.execute(
            "SELECT slug, email, server_imap, server_smtp, created_at FROM mailbox_accounts WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [{"slug": r["slug"], "email": r["email"], "server_imap": r["server_imap"],
             "server_smtp": r["server_smtp"], "created_at": r["created_at"]} for r in rows]


@app.delete("/api/mailbox/account")
def mailbox_account_delete(slug: str, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    with db.conn() as c:
        cur = c.execute("DELETE FROM mailbox_accounts WHERE user_id=? AND slug=?", (user_id, slug))
        if cur.rowcount == 0:
            raise HTTPException(404, "account not found")
    db.audit(user_id, "delete_account", slug)
    return {"ok": True}


def _load_account(user_id: str, slug: str) -> dict:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM mailbox_accounts WHERE user_id=? AND slug=?",
            (user_id, slug),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"account slug={slug} not found for current user")
    return {
        "email": row["email"],
        "server_imap": row["server_imap"], "port_imap": row["port_imap"],
        "server_smtp": row["server_smtp"], "port_smtp": row["port_smtp"],
        "password": decrypt(user_id, row["app_password_enc"]),
    }


class ListReq(BaseModel):
    slug: str
    limit: int = Field(20, ge=1, le=100)
    unread_only: bool = False
    since: str | None = None


@app.post("/api/mailbox/list")
def mailbox_list(req: ListReq, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    a = _load_account(user_id, req.slug)
    headers = imap_client.list_inbox(
        a["server_imap"], a["port_imap"], a["email"], a["password"],
        limit=req.limit, unread_only=req.unread_only, since=req.since,
    )
    db.audit(user_id, "list", req.slug, f"limit={req.limit} unread={req.unread_only}")
    return [{"uid": h.uid, "msg_id": h.msg_id, "from": h.from_addr, "subject": h.subject,
             "date": h.date, "flags": h.flags, "size": h.size} for h in headers]


class ReadReq(BaseModel):
    slug: str
    uid: str


@app.post("/api/mailbox/read")
def mailbox_read(req: ReadReq, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    a = _load_account(user_id, req.slug)
    msg = imap_client.read_message(
        a["server_imap"], a["port_imap"], a["email"], a["password"], uid=req.uid,
    )
    db.audit(user_id, "read", req.slug, f"uid={req.uid}")
    return {"uid": msg.uid, "headers": msg.headers, "body_plain": msg.body_plain,
            "body_html": msg.body_html, "attachments": msg.attachments}


class DraftReq(BaseModel):
    slug: str
    to: EmailStr
    cc: str | None = None
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=20000)
    in_reply_to: str | None = None


@app.post("/api/mailbox/draft")
def mailbox_draft(req: DraftReq, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    _load_account(user_id, req.slug)
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO drafts (user_id, account_slug, to_addr, cc_addr, subject, body, in_reply_to, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, req.slug, str(req.to), req.cc, req.subject, req.body, req.in_reply_to, db.now_ts()),
        )
        draft_id = cur.lastrowid
    db.audit(user_id, "draft", req.slug, f"draft_id={draft_id} to={req.to}")
    return {"draft_id": draft_id, "preview": {
        "to": req.to, "cc": req.cc, "subject": req.subject,
        "body": req.body[:500] + ("..." if len(req.body) > 500 else ""),
    }, "hint": "用户确认无误后调 /api/mailbox/send 真发 (必须传 user_confirmed=true)"}


class SendReq(BaseModel):
    draft_id: int
    user_confirmed: bool = Field(..., description="必须显式 true · 防意外")


@app.post("/api/mailbox/send")
def mailbox_send(req: SendReq, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    if not req.user_confirmed:
        raise HTTPException(400, "must explicitly set user_confirmed=true (硬规则 · 不自动发)")
    with db.conn() as c:
        d = c.execute(
            "SELECT * FROM drafts WHERE id=? AND user_id=?",
            (req.draft_id, user_id),
        ).fetchone()
    if not d:
        raise HTTPException(404, "draft not found (or wrong user)")
    if d["sent_at"]:
        raise HTTPException(400, "draft already sent")
    a = _load_account(user_id, d["account_slug"])
    msg_id = smtp_client.send_message(
        a["server_smtp"], a["port_smtp"], a["email"], a["password"],
        to=d["to_addr"], cc=d["cc_addr"], subject=d["subject"], body=d["body"],
        in_reply_to=d["in_reply_to"],
    )
    with db.conn() as c:
        c.execute(
            "UPDATE drafts SET sent_at=?, sent_msg_id=? WHERE id=?",
            (db.now_ts(), msg_id, req.draft_id),
        )
    db.audit(user_id, "send", d["account_slug"], f"draft_id={req.draft_id} msg_id={msg_id}")
    return {"ok": True, "msg_id": msg_id, "sent_at": db.now_ts()}


class ArchiveReq(BaseModel):
    slug: str
    uid: str
    label: str = "Archived"


@app.post("/api/mailbox/archive")
def mailbox_archive(req: ArchiveReq, x_user_id: str | None = Header(None)):
    user_id = require_user(x_user_id)
    a = _load_account(user_id, req.slug)
    ok = imap_client.archive(
        a["server_imap"], a["port_imap"], a["email"], a["password"],
        uid=req.uid, label=req.label,
    )
    db.audit(user_id, "archive", req.slug, f"uid={req.uid} label={req.label}")
    return {"ok": ok}


# ─── /api/chat · 全对话形态 (老板 5-6 拍 · 替表单) ──────────────────────


class ChatReq(BaseModel):
    messages: list[dict] = Field(..., description="[{role, content}] · 跟 OpenAI / qwen 同 schema")


@app.post("/api/chat")
def chat(req: ChatReq, x_user_id: str | None = Header(None)):
    """跟 dayou 自然语言聊 · LLM 决定调啥 tool · 不要表单。

    用户场景示例:
    - "帮我挂个 Gmail" → LLM 引导对话获取 4 件 → 调 connect_mailbox
    - "看下今早邮件" → 调 list_inbox · 摘要返回
    - "回他确认下周二" → 调 draft_message · 返草稿 · 等用户说"发"再调 send_draft
    """
    user_id = require_user(x_user_id)
    if not req.messages:
        raise HTTPException(400, "messages 不能空")
    return chat_turn(user_id, req.messages)
