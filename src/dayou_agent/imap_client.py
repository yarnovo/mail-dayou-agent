"""IMAP 收信 · 用 stdlib imaplib + email · 标准协议 · 主流邮箱通吃。

per-user · per-account 凭证从 db 拿 · Fernet 解密 · 用完不缓存。
"""
from __future__ import annotations
import email
import email.header
import imaplib
import ssl
from dataclasses import dataclass


@dataclass
class MailHeader:
    uid: str
    msg_id: str
    from_addr: str
    subject: str
    date: str
    flags: list[str]
    size: int


@dataclass
class MailContent:
    uid: str
    headers: dict[str, str]
    body_plain: str
    body_html: str
    attachments: list[dict]  # [{filename, content_type, size}]


def _decode_header(raw: str) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    return "".join(
        (p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p)
        for p, c in parts
    )


def _connect(server: str, port: int, email_addr: str, password: str) -> imaplib.IMAP4_SSL:
    """SSL on default 993 · 主流邮箱都支持。"""
    ctx = ssl.create_default_context()
    m = imaplib.IMAP4_SSL(server, port, ssl_context=ctx)
    m.login(email_addr, password)
    return m


def verify_credentials(server: str, port: int, email_addr: str, password: str) -> tuple[bool, str]:
    """connect 验证 · 返回 (ok, msg)."""
    try:
        m = _connect(server, port, email_addr, password)
        m.select("INBOX", readonly=True)
        m.logout()
        return True, "verified"
    except imaplib.IMAP4.error as e:
        return False, f"IMAP 登录失败: {e}"
    except Exception as e:
        return False, f"连接失败: {e!r}"


def list_inbox(
    server: str, port: int, email_addr: str, password: str,
    *, limit: int = 20, unread_only: bool = False, since: str | None = None,
) -> list[MailHeader]:
    """拉收件箱 · 默认最近 limit 封 · 倒序 (最新在前)."""
    m = _connect(server, port, email_addr, password)
    try:
        m.select("INBOX", readonly=True)
        criteria = []
        if unread_only:
            criteria.append("UNSEEN")
        if since:
            # since 格式: "01-Jan-2026"
            criteria.append(f'SINCE "{since}"')
        criteria_str = " ".join(criteria) if criteria else "ALL"
        typ, data = m.uid("SEARCH", None, criteria_str)
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()[-limit:][::-1]  # 最新 limit 个
        results: list[MailHeader] = []
        for uid in uids:
            uid_s = uid.decode()
            typ, msg_data = m.uid("FETCH", uid, "(BODY.PEEK[HEADER] FLAGS RFC822.SIZE)")
            if typ != "OK":
                continue
            # msg_data: [(b'1 (FLAGS ...)', b'<headers...>'), b')']
            flags_raw = b""
            header_raw = b""
            size = 0
            for item in msg_data:
                if isinstance(item, tuple):
                    flags_raw = item[0]
                    header_raw = item[1] if len(item) > 1 else b""
            msg = email.message_from_bytes(header_raw)
            # parse FLAGS + SIZE
            flags = []
            try:
                fs = flags_raw.decode("utf-8", errors="ignore")
                if "FLAGS (" in fs:
                    flags = fs.split("FLAGS (")[1].split(")")[0].split()
                if "RFC822.SIZE " in fs:
                    size = int(fs.split("RFC822.SIZE ")[1].split(" ")[0].rstrip(")"))
            except Exception:
                pass
            results.append(MailHeader(
                uid=uid_s,
                msg_id=msg.get("Message-ID", ""),
                from_addr=_decode_header(msg.get("From", "")),
                subject=_decode_header(msg.get("Subject", "(无主题)")),
                date=msg.get("Date", ""),
                flags=flags,
                size=size,
            ))
        return results
    finally:
        try: m.logout()
        except Exception: pass


def read_message(
    server: str, port: int, email_addr: str, password: str, *, uid: str,
) -> MailContent:
    """读单封原文 + 附件清单 (附件不下载内容 · 只列 metadata)."""
    m = _connect(server, port, email_addr, password)
    try:
        m.select("INBOX", readonly=True)
        typ, data = m.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            raise ValueError(f"uid {uid} 不存在")
        raw = data[0][1]
        msg = email.message_from_bytes(raw)
        headers = {
            "From": _decode_header(msg.get("From", "")),
            "To": _decode_header(msg.get("To", "")),
            "Cc": _decode_header(msg.get("Cc", "")),
            "Subject": _decode_header(msg.get("Subject", "")),
            "Date": msg.get("Date", ""),
            "Message-ID": msg.get("Message-ID", ""),
        }
        body_plain = ""
        body_html = ""
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = part.get("Content-Disposition", "")
                if "attachment" in disp.lower():
                    attachments.append({
                        "filename": _decode_header(part.get_filename() or ""),
                        "content_type": ctype,
                        "size": len(part.get_payload(decode=True) or b""),
                    })
                elif ctype == "text/plain" and not body_plain:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    body_plain = payload.decode(charset, errors="replace")
                elif ctype == "text/html" and not body_html:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    body_html = payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                body_html = text
            else:
                body_plain = text
        return MailContent(uid=uid, headers=headers, body_plain=body_plain, body_html=body_html, attachments=attachments)
    finally:
        try: m.logout()
        except Exception: pass


def archive(
    server: str, port: int, email_addr: str, password: str, *, uid: str, label: str = "Archived",
) -> bool:
    """打 IMAP label (Gmail 用 X-GM-LABELS · 其他走 COPY 到自定义 mailbox)."""
    m = _connect(server, port, email_addr, password)
    try:
        m.select("INBOX")
        # 简化版: COPY 到 [Archive] 文件夹 + STORE Seen
        try:
            m.uid("COPY", uid, label)
        except Exception:
            # 文件夹不存在 → 建
            m.create(label)
            m.uid("COPY", uid, label)
        m.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        m.expunge()
        return True
    finally:
        try: m.logout()
        except Exception: pass
