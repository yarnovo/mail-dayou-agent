"""LLM chat endpoint · qwen-plus tool calling · dayou 全对话形态。

设计:
- 用户跟 dayou 聊 · LLM 决定调啥 tool (connect/list/read/draft/send/archive)
- 多轮对话 · LLM 增量收集 args (邮箱地址 → 应用密码 → 自动调 connect_mailbox)
- 草稿先给用户看 · 用户说"发"才真发 · 跟 SOUL.md 一致

system prompt 来自 workspace/dayou/SOUL.md + IDENTITY.md (沉稳秘书腔 · 不替用户拍 · 永远不自动回)。
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import dashscope
from dashscope import Generation

from . import db, imap_client, smtp_client
from .crypto import encrypt, decrypt
from .providers import guess_provider


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace" / "dayou"


def _load_system_prompt() -> str:
    """读 IDENTITY + SOUL · 拼成 system prompt."""
    parts = ["你是阿空大邮 (邮箱管理大师) · 帮用户管他自己的邮箱。\n"]
    for name in ("IDENTITY.md", "SOUL.md"):
        p = WORKSPACE / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    parts.append("""
关键规则 (必守):
- 永远不自动回信 · 任何对外发信都先草稿 + 等用户说"发"才调 send_draft
- 永远不删信 · 只能 archive_message (打 label · 不真删)
- 用户说"挂邮箱" → 引导他给 4 件: 邮箱地址 / 服务器 (你能从域名自动判断 · 见 guess_provider) / 应用专用密码 / 昵称(slug)
- 强制应用专用密码 · 拒绝主密码
- 用户给的密码 · 你立刻调 connect_mailbox · 验证通过才确认挂上
- 用 tool 时直接调 · 不要解释你要调啥 (用户不关心)
""")
    return "\n".join(parts)


def _resolve_dashscope_key() -> str:
    if k := os.getenv("DASHSCOPE_API_KEY"):
        return k
    secrets_path = REPO_ROOT / ".vault" / "secrets.json"
    if secrets_path.exists():
        d = json.loads(secrets_path.read_text())
        if k := d.get("dashscope-main", {}).get("api_key"):
            return k
    raise RuntimeError("DASHSCOPE_API_KEY 没配 · 走 env 或 .vault/secrets.json::dashscope-main.api_key")


# ─── Tools 定义 (qwen-plus function calling 格式) ─────────────────────────

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


# ─── tool 实现 (per user_id 隔离) ────────────────────────────────────────


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


# ─── chat 主入口 ─────────────────────────────────────────────────────────


def chat_turn(user_id: str, messages: list[dict]) -> dict:
    """单轮 chat · 返回 {reply, tool_calls} · 调用方决定要不要再轮。

    messages: [{role: 'user'|'assistant'|'system'|'tool', content: ...}]
    返 {reply: str, used_tools: [{name, args, result}]} (tool_calls 内化 · 用户只看自然回复)
    """
    dashscope.api_key = _resolve_dashscope_key()
    sys_prompt = _load_system_prompt()
    full_msgs = [{"role": "system", "content": sys_prompt}] + messages
    used_tools: list[dict] = []

    # 最多 5 轮 tool calling (防死循环)
    for _ in range(5):
        rsp = Generation.call(
            model="qwen-plus",
            messages=full_msgs,
            tools=TOOLS,
            result_format="message",
        )
        if rsp.status_code != 200:
            return {"reply": f"LLM 出错: {rsp.message}", "used_tools": used_tools}
        choice = rsp.output.choices[0]
        msg = choice.message
        full_msgs.append(dict(msg))

        tcs = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        if not tcs:
            # 终态: 没 tool · 拿最终自然语言回复
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            return {"reply": content or "", "used_tools": used_tools}

        # 执行所有 tool calls
        for tc in tcs:
            fn = tc["function"] if isinstance(tc, dict) else tc.function
            name = fn["name"] if isinstance(fn, dict) else fn.name
            raw_args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}
            impl = TOOL_IMPLS.get(name)
            if not impl:
                result = {"error": f"unknown tool {name}"}
            else:
                try:
                    result = impl(args, user_id)
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
            used_tools.append({"name": name, "args": args, "result": result})
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
            full_msgs.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        # 再轮 · 把 tool 结果给 LLM 让它合成最终回复

    return {"reply": "(LLM 调 tool 超 5 轮 · 异常)", "used_tools": used_tools}
