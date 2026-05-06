"""SMTP 发信 · 用 stdlib smtplib + email · 标准协议."""
from __future__ import annotations
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid


def send_message(
    server: str, port: int, email_addr: str, password: str,
    *, to: str, subject: str, body: str,
    cc: str | None = None,
    in_reply_to: str | None = None,
    from_name: str | None = None,
) -> str:
    """SMTP 真发 · 返回 Message-ID。"""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{email_addr}>" if from_name else email_addr
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg_id = make_msgid(domain=email_addr.split("@")[-1])
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.attach(MIMEText(body, "plain", "utf-8"))

    recipients = [to]
    if cc:
        recipients.extend([c.strip() for c in cc.split(",")])

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(server, port, context=ctx, timeout=30) as s:
            s.login(email_addr, password)
            s.sendmail(email_addr, recipients, msg.as_string())
    else:
        # 587 STARTTLS
        with smtplib.SMTP(server, port, timeout=30) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(email_addr, password)
            s.sendmail(email_addr, recipients, msg.as_string())
    return msg_id
