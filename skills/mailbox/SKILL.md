---
name: mailbox
description: 邮箱管理 · 帮用户挂 IMAP/SMTP / 列收件箱 / 读单封 / 起草回信 / 真发 / 归档
---

# mailbox skill

mail-dayou (阿空大邮) 的核心 skill · 提供 7 个 tool:

- `guess_provider`: 邮箱域名 → IMAP/SMTP server + 应用密码指引
- `connect_mailbox`: 验证 IMAP 凭证 + Fernet 加密存 sqlite
- `list_inbox`: 拉收件箱 headers
- `read_message`: 读单封原文 + 摘要
- `draft_message`: 起草 (不真发)
- `send_draft`: 真发 SMTP (必用户显式确认)
- `archive_message`: 归档 (打 label · 不删信)

数据 schema (mailbox_accounts + drafts) 在 mail-dayou-agent/src/dayou_agent/db.py · 启动时 db.exec_script() 注入。
凭证 Fernet (per-user HKDF derived key) · master_key 走 env DAYOU_MASTER_KEY (留 dayou-specific · 不用 lib crypto)。
