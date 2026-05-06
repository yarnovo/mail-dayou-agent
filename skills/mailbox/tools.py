"""mail-dayou mailbox skill · 7 tools (guess/connect/list/read/draft/send/archive)。

import 走 dayou_agent 包 (本 skill 是 mail-dayou-agent 仓内的 · 不是独立仓)。
LLMRunner 加载 skill 时执行此文件 · TOOLS + TOOL_IMPLS 模块级变量被读。
"""
from __future__ import annotations

from dayou_agent import db, imap_client, smtp_client
from dayou_agent.crypto import encrypt, decrypt
from dayou_agent.providers import guess_provider


# ─── Tools 定义 (function calling JSON · LLMRunner 透传给 LLM) ─────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "guess_provider",
            "description": "根据邮箱域名自动推断 IMAP/SMTP server + port + 应用密码建立指引 URL · 用户给邮箱地址后立刻调",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string", "description": "用户邮箱地址 e.g. me@gmail.com"}},
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connect_mailbox",
            "description": "挂邮箱 · 验证 IMAP 凭证通过后存 sqlite (Fernet 加密) · 用户给齐 4 件 (slug/email/server/app_password) 后调",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "用户取的昵称 e.g. work / personal"},
                    "email": {"type": "string"},
                    "server_imap": {"type": "string"},
                    "port_imap": {"type": "integer"},
                    "server_smtp": {"type": "string"},
                    "port_smtp": {"type": "integer"},
                    "app_password": {"type": "string", "description": "应用专用密码 · 不能是主密码"},
                },
                "required": ["slug", "email", "server_imap", "port_imap", "server_smtp", "port_smtp", "app_password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_inbox",
            "description": "拉收件箱 headers · 返回最近 N 封 (默认 20) · from / subject / date / unread 状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                    "unread_only": {"type": "boolean", "default": False},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_message",
            "description": "读单封原文 + 摘要 · uid 从 list_inbox 返回的 headers 取",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}, "uid": {"type": "string"}},
                "required": ["slug", "uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_message",
            "description": "起草新信 / 回信 · 不真发 · 返回 draft_id 给用户看 · 用户说'发'才调 send_draft",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "in_reply_to": {"type": "string", "description": "回信时填原邮件 Message-ID"},
                },
                "required": ["slug", "to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": "真发 SMTP · 必须用户显式说'发' · 否则不调",
            "parameters": {
                "type": "object",
                "properties": {"draft_id": {"type": "integer"}},
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_message",
            "description": "归档邮件 (打 label · 不删)",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}, "uid": {"type": "string"}, "label": {"type": "string", "default": "Archived"}},
                "required": ["slug", "uid"],
            },
        },
    },
]


# ─── tool 实现 (per user_id 隔离 · 调 imap/smtp + sqlite) ────────────


def _load_account(user_id: str, slug: str) -> dict | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM mailbox_accounts WHERE user_id=? AND slug=?",
            (user_id, slug),
        ).fetchone()
    if not row:
        return None
    return {
        "email": row["email"],
        "server_imap": row["server_imap"], "port_imap": row["port_imap"],
        "server_smtp": row["server_smtp"], "port_smtp": row["port_smtp"],
        "password": decrypt(user_id, row["app_password_enc"]),
    }


def _tool_guess_provider(args: dict, user_id: str) -> dict:
    p = guess_provider(args["email"])
    if not p:
        return {"matched": False, "hint": "服务商不在内置表 · 请用户手动给 IMAP/SMTP server"}
    return {
        "matched": True, "name": p.name,
        "imap_host": p.imap_host, "imap_port": p.imap_port,
        "smtp_host": p.smtp_host, "smtp_port": p.smtp_port,
        "app_password_help": p.app_password_help,
        "app_password_url": p.app_password_url,
    }


def _tool_connect_mailbox(args: dict, user_id: str) -> dict:
    ok, msg = imap_client.verify_credentials(
        args["server_imap"], args["port_imap"], args["email"], args["app_password"]
    )
    if not ok:
        return {"ok": False, "error": msg}
    enc = encrypt(user_id, args["app_password"])
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
            (user_id, args["slug"], args["email"], args["server_imap"], args["port_imap"],
             args["server_smtp"], args["port_smtp"], enc, db.now_ts()),
        )
    db.audit(user_id, "connect", args["slug"], args["email"])
    return {"ok": True, "slug": args["slug"], "email": args["email"]}


def _tool_list_inbox(args: dict, user_id: str) -> dict:
    a = _load_account(user_id, args["slug"])
    if not a:
        return {"error": f"邮箱 slug={args['slug']} 没挂 · 让用户先挂"}
    headers = imap_client.list_inbox(
        a["server_imap"], a["port_imap"], a["email"], a["password"],
        limit=args.get("limit", 20), unread_only=args.get("unread_only", False),
    )
    db.audit(user_id, "list", args["slug"])
    return {"count": len(headers), "messages": [
        {"uid": h.uid, "from": h.from_addr, "subject": h.subject, "date": h.date, "flags": h.flags}
        for h in headers
    ]}


def _tool_read_message(args: dict, user_id: str) -> dict:
    a = _load_account(user_id, args["slug"])
    if not a: return {"error": f"邮箱 slug={args['slug']} 没挂"}
    msg = imap_client.read_message(
        a["server_imap"], a["port_imap"], a["email"], a["password"], uid=args["uid"],
    )
    db.audit(user_id, "read", args["slug"], f"uid={args['uid']}")
    return {
        "uid": msg.uid, "from": msg.headers.get("From"),
        "subject": msg.headers.get("Subject"), "date": msg.headers.get("Date"),
        "message_id": msg.headers.get("Message-ID"),
        "body": msg.body_plain[:3000] if msg.body_plain else "(无文本正文)",
    }


def _tool_draft_message(args: dict, user_id: str) -> dict:
    a = _load_account(user_id, args["slug"])
    if not a: return {"error": f"邮箱 slug={args['slug']} 没挂"}
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO drafts (user_id, account_slug, to_addr, cc_addr, subject, body, in_reply_to, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, args["slug"], args["to"], None, args["subject"], args["body"], args.get("in_reply_to"), db.now_ts()),
        )
        draft_id = cur.lastrowid
    db.audit(user_id, "draft", args["slug"], f"draft_id={draft_id}")
    return {"draft_id": draft_id, "to": args["to"], "subject": args["subject"], "body_preview": args["body"][:300]}


def _tool_send_draft(args: dict, user_id: str) -> dict:
    with db.conn() as c:
        d = c.execute(
            "SELECT * FROM drafts WHERE id=? AND user_id=?",
            (args["draft_id"], user_id),
        ).fetchone()
    if not d: return {"error": "draft not found"}
    if d["sent_at"]: return {"error": "已发过"}
    a = _load_account(user_id, d["account_slug"])
    if not a: return {"error": f"邮箱 slug={d['account_slug']} 没挂"}
    msg_id = smtp_client.send_message(
        a["server_smtp"], a["port_smtp"], a["email"], a["password"],
        to=d["to_addr"], cc=d["cc_addr"], subject=d["subject"], body=d["body"],
        in_reply_to=d["in_reply_to"],
    )
    with db.conn() as c:
        c.execute("UPDATE drafts SET sent_at=?, sent_msg_id=? WHERE id=?",
                  (db.now_ts(), msg_id, args["draft_id"]))
    db.audit(user_id, "send", d["account_slug"], f"msg_id={msg_id}")
    return {"ok": True, "msg_id": msg_id}


def _tool_archive_message(args: dict, user_id: str) -> dict:
    a = _load_account(user_id, args["slug"])
    if not a: return {"error": f"邮箱 slug={args['slug']} 没挂"}
    ok = imap_client.archive(
        a["server_imap"], a["port_imap"], a["email"], a["password"],
        uid=args["uid"], label=args.get("label", "Archived"),
    )
    db.audit(user_id, "archive", args["slug"], f"uid={args['uid']}")
    return {"ok": ok}


TOOL_IMPLS = {
    "guess_provider": _tool_guess_provider,
    "connect_mailbox": _tool_connect_mailbox,
    "list_inbox": _tool_list_inbox,
    "read_message": _tool_read_message,
    "draft_message": _tool_draft_message,
    "send_draft": _tool_send_draft,
    "archive_message": _tool_archive_message,
}
