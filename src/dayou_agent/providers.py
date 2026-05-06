"""主流邮箱服务商 IMAP/SMTP 配置 · 用户给 email 域名 · 自动推断 server / port / SSL。

用户输入 you@gmail.com → 自动建议 imap.gmail.com:993 + smtp.gmail.com:465。
不在表里的 (自建 dovecot 等) · 用户手动填。
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class MailProvider:
    name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    app_password_help: str  # 给用户的应用密码建立指引
    app_password_url: str   # 直接跳转


PROVIDERS: dict[str, MailProvider] = {
    "gmail.com": MailProvider(
        name="Gmail",
        imap_host="imap.gmail.com", imap_port=993,
        smtp_host="smtp.gmail.com", smtp_port=465,
        app_password_help="Google 账号 → 安全 → 两步验证 → 应用专用密码 (16 位 · 无空格)",
        app_password_url="https://myaccount.google.com/apppasswords",
    ),
    "outlook.com": MailProvider(
        name="Outlook / Hotmail / Live",
        imap_host="outlook.office365.com", imap_port=993,
        smtp_host="smtp.office365.com", smtp_port=587,
        app_password_help="Microsoft 账号 → 安全 → 高级安全选项 → 应用密码",
        app_password_url="https://account.microsoft.com/security",
    ),
    "hotmail.com": MailProvider(
        name="Hotmail (= Outlook)",
        imap_host="outlook.office365.com", imap_port=993,
        smtp_host="smtp.office365.com", smtp_port=587,
        app_password_help="Microsoft 账号 → 安全 → 应用密码",
        app_password_url="https://account.microsoft.com/security",
    ),
    "qq.com": MailProvider(
        name="QQ 邮箱",
        imap_host="imap.qq.com", imap_port=993,
        smtp_host="smtp.qq.com", smtp_port=465,
        app_password_help="QQ 邮箱 → 设置 → 账户 → POP3/IMAP/SMTP → 开启 + 生成授权码 (16 位)",
        app_password_url="https://mail.qq.com/cgi-bin/frame_html?sid=&r=&url=/cgi-bin/setting10",
    ),
    "163.com": MailProvider(
        name="163 网易邮箱",
        imap_host="imap.163.com", imap_port=993,
        smtp_host="smtp.163.com", smtp_port=465,
        app_password_help="163 邮箱 → 设置 → POP3/SMTP/IMAP → 开启 + 客户端授权密码",
        app_password_url="https://mail.163.com/",
    ),
    "126.com": MailProvider(
        name="126 网易邮箱",
        imap_host="imap.126.com", imap_port=993,
        smtp_host="smtp.126.com", smtp_port=465,
        app_password_help="126 邮箱 → 设置 → POP3/SMTP/IMAP → 客户端授权密码",
        app_password_url="https://mail.126.com/",
    ),
    "yeah.net": MailProvider(
        name="Yeah 网易邮箱",
        imap_host="imap.yeah.net", imap_port=993,
        smtp_host="smtp.yeah.net", smtp_port=465,
        app_password_help="Yeah 邮箱 → 设置 → POP3/SMTP/IMAP → 客户端授权密码",
        app_password_url="https://mail.yeah.net/",
    ),
    "akongtech.cn": MailProvider(
        name="腾讯企业邮 (akongtech.cn)",
        imap_host="imap.exmail.qq.com", imap_port=993,
        smtp_host="smtp.exmail.qq.com", smtp_port=465,
        app_password_help="腾讯企业邮 → 设置 → 客户端密码 (跟主密码不同)",
        app_password_url="https://exmail.qq.com/cgi-bin/loginpage",
    ),
    "icloud.com": MailProvider(
        name="iCloud Mail",
        imap_host="imap.mail.me.com", imap_port=993,
        smtp_host="smtp.mail.me.com", smtp_port=587,
        app_password_help="Apple ID → 登录与安全 → App 专用密码",
        app_password_url="https://appleid.apple.com/account/manage",
    ),
    "yahoo.com": MailProvider(
        name="Yahoo Mail",
        imap_host="imap.mail.yahoo.com", imap_port=993,
        smtp_host="smtp.mail.yahoo.com", smtp_port=465,
        app_password_help="Yahoo 账号 → 账号安全 → 生成 App password",
        app_password_url="https://login.yahoo.com/account/security",
    ),
}


def guess_provider(email: str) -> MailProvider | None:
    """根据 email 域名匹配预设 · 不命中返 None (用户手动填)."""
    domain = email.lower().split("@")[-1] if "@" in email else ""
    return PROVIDERS.get(domain)
