"""阿空大邮 (mail-dayou) FastAPI · multi-user IMAP/SMTP 邮箱管理。

通用部分走 akong-agent-base v0.2 (db / chat_store / middleware / LLMRunner / register_chat_routes / Skill)。
本仓只管 dayou-specific:
- crypto.py (info=dayou-user-key-v1 · 兼容老 app_password_enc · 不用 lib crypto)
- providers / imap_client / smtp_client (邮箱业务)
- skills/mailbox/ (skill 文件夹 · 7 个 tool · LLMRunner discover 加载)

agent 启动:
  skills = discover_skills(repo_root / "skills")
  runner = LLMRunner(skills=skills, base_prompt_loader=load_persona)

部署 env (deploy.yml):
- AGENT_NAS_ROOT=/mnt/nas/dayou
- AGENT_DB_NAME=dayou.sqlite
- AGENT_USE_NAS=1
"""
from __future__ import annotations
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from akong_agent_base import LLMRunner, register_chat_routes, require_user, discover_skills

from . import db, imap_client, smtp_client
from .crypto import encrypt, decrypt
from .providers import guess_provider, PROVIDERS


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace" / "dayou"
SKILLS_DIR = REPO_ROOT / "skills"


def _load_persona() -> str:
    """读 workspace/dayou/IDENTITY.md + SOUL.md · 拼基础 prompt (skill prompts 由 LLMRunner 自动 append)."""
    parts = ["你是阿空大邮 (邮箱管理大师) · 帮用户管他自己的邮箱。\n"]
    for name in ("IDENTITY.md", "SOUL.md"):
        p = WORKSPACE / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


app = FastAPI(title="mail-dayou-agent", version="0.3.0",
              description="阿空大邮 · multi-user 邮箱管理 · 用 akong-agent-base v0.2 skill 系统")

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
    db.init()  # 通用 chat schema (lib) + dayou 表 (mailbox_accounts + drafts)


# ─── chat endpoint (lib 自动挂 3 个 · 用 skill driven LLMRunner) ──────────

_runner = LLMRunner(
    skills=discover_skills(SKILLS_DIR),
    base_prompt_loader=_load_persona,
    model="deepseek-v4-pro",
)
register_chat_routes(app, _runner)  # POST /api/chat · GET /api/chat/history · POST /api/chat/topic-break


@app.get("/health")
def health():
    skill_names = [s.name for s in _runner.skills]
    return {
        "status": "ok", "agent": "dayou", "version": "0.3.0",
        "multi_user": True, "lib": "akong-agent-base@0.2.0",
        "skills": skill_names,
    }


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


# ─── mailbox endpoints (dayou-specific REST · 跟 skill tool 平行 · 给前端直调) ──


class ConnectReq(BaseModel):
    slug: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    server_imap: str
    port_imap: int = 993
    server_smtp: str
    port_smtp: int = 465
    app_password: str = Field(..., min_length=4, max_length=128)


@app.post("/api/mailbox/connect")
def mailbox_connect(req: ConnectReq, user_id: str = Depends(require_user)):
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
def mailbox_accounts(user_id: str = Depends(require_user)):
    with db.conn() as c:
        rows = c.execute(
            "SELECT slug, email, server_imap, server_smtp, created_at FROM mailbox_accounts WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [{"slug": r["slug"], "email": r["email"], "server_imap": r["server_imap"],
             "server_smtp": r["server_smtp"], "created_at": r["created_at"]} for r in rows]


@app.delete("/api/mailbox/account")
def mailbox_account_delete(slug: str, user_id: str = Depends(require_user)):
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
def mailbox_list(req: ListReq, user_id: str = Depends(require_user)):
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
def mailbox_read(req: ReadReq, user_id: str = Depends(require_user)):
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
def mailbox_draft(req: DraftReq, user_id: str = Depends(require_user)):
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
def mailbox_send(req: SendReq, user_id: str = Depends(require_user)):
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
def mailbox_archive(req: ArchiveReq, user_id: str = Depends(require_user)):
    a = _load_account(user_id, req.slug)
    ok = imap_client.archive(
        a["server_imap"], a["port_imap"], a["email"], a["password"],
        uid=req.uid, label=req.label,
    )
    db.audit(user_id, "archive", req.slug, f"uid={req.uid} label={req.label}")
    return {"ok": ok}
