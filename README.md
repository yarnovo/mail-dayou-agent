# mail-dayou-agent

阿空大邮 (邮箱管理大师) backend · 帮用户**收发管理自己的邮箱** (IMAP + SMTP)。

## 老板视角

- prod: https://api.dayou.mail.agentaily.com (待 FC v3 部署)
- staging: https://staging.api.dayou.mail.agentaily.com

## 它是谁

C 端用户挂自己的邮箱 (Gmail / 腾讯企业邮 / Outlook / 163 / 自建) → agent 帮:

- 拉收件箱 + 摘要 + 提取行动项
- 起草回信 (用户 confirm 才发 · 不自动回)
- 主动写新信 (用户口述 → 草稿 → confirm → SMTP 发)
- 归档 / 打 label / 标已读 (不删 · 永久删除高风险)
- 自然语言搜信

## 安全底线

- **凭证**: 强制用户配**应用专用密码** · 不存原始密码 · 凭证只在用户当前会话内解密 · 退出清内存
- **不自动回信**: 任何对外发信都先 draft + 用户 confirm
- **不删信**: 只归档 / label · 用户要永久删自己点
- **不上传邮件正文**到 LLM 服务商: 摘要在 LLM call 后立即丢内存
- **多账号**: 每账号独立 vault namespace · 不串

## 跑

```bash
uv sync
vault install                                 # → .vault/secrets.json
uv run uvicorn dayou_agent.server:app --host 0.0.0.0 --port 9000

# 测
curl localhost:9000/health
curl -X POST localhost:9000/api/mailbox/list \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "u-001", "account_slug": "main", "since": "2026-05-01", "limit": 20}'
```

## API (v0.1 草案)

| Method | Path | 描述 |
|---|---|---|
| GET  | `/health` | 健康检查 |
| POST | `/api/mailbox/connect` | 配新邮箱 (server/port/email/app_password) → 验通过存 vault |
| POST | `/api/mailbox/list` | 拉收件箱列表 (按时间/未读/发件人过滤) |
| POST | `/api/mailbox/read` | 单封原文 + 摘要 + 行动项 |
| POST | `/api/mailbox/draft` | 起草新信 / 回信 (return draft · 不发) |
| POST | `/api/mailbox/send` | 真发 (要传 draft_id + user_confirmed=true) |
| POST | `/api/mailbox/archive` | 归档 / 打 label (不删) |
| POST | `/api/mailbox/search` | 自然语言搜 |

## 部署

走 `fc-agent-deploy` skill (FC v3 custom container)：

```bash
SUB=mail-dayou FC_FUNCTION=mail-dayou-agent bash ~/.claude/skills/fc-agent-deploy/templates/setup.sh
```

push develop → staging · push main → prod (GHA)。

## 跟其他仓

- 上游: dashscope-main (vault) · qwen-plus 摘要 + qwen3-max 起草
- 下游: mail-dayou-chat (用户对话)
- 同公司: mail-dayou-intro (静态站) / mail-dayou-script (口播稿) / mail-dayou-e2e

## v0.1 范围

- ✅ /health
- ✅ IMAP 收 (list / read / search)
- ✅ SMTP 发 (draft / send · 强制 user_confirmed)
- ✅ 多账号 (per user · per slug)
- ✅ 应用专用密码 (Gmail 应用密码 / exmail 客户端密码 / Outlook IMAP password)
- ❌ OAuth 2.0 (Gmail / Outlook · v0.2)
- ❌ AI 行动项提取 (v0.2 接 qwen-plus)
- ❌ 自然语言搜信 (v0.2 接 bge-m3 + sqlite vec)
- ❌ 计费 (v0.3 · 按发信封数 / 摘要 token)

## CONTRACTS

- IMAP: 主流邮箱服务商 RFC 3501 兼容 (Gmail / exmail / Outlook / 163 / 126 / qq / Yahoo / iCloud / 自建 dovecot)
- SMTP: RFC 5321 (587 STARTTLS / 465 SSL)
- 服务器矩阵 (内置): 见 `src/dayou_agent/providers.json` (常用域名自动推断 server / port)
- vault namespace: `mailbox/<user_id>/<slug>` 存 server/port/email/app_password (加密)
