"""Fernet 对称加密 · 用户应用密码加密存 sqlite。

master key 来源:
- prod: FC env DAYOU_MASTER_KEY (vault inject · 32 字节 base64)
- dev:  .vault/secrets.json `dayou.master_key`

策略:
- 加密时只用 master_key + user_id 做 derived key (HKDF) · 不同用户不同 cipher
- 即使 sqlite 泄漏 · 攻击者还要拿到 master_key 才能解
"""
from __future__ import annotations
import base64
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _master_key() -> bytes:
    raw = os.environ.get("DAYOU_MASTER_KEY")
    if raw:
        return base64.urlsafe_b64decode(raw)
    # dev fallback · vault secrets.json
    try:
        import json
        from pathlib import Path
        p = Path(".vault/secrets.json")
        if p.exists():
            data = json.loads(p.read_text())
            v = data.get("dayou", {}).get("master_key")
            if v:
                return base64.urlsafe_b64decode(v)
    except Exception:
        pass
    raise RuntimeError(
        "DAYOU_MASTER_KEY 未配 · prod 走 FC env · dev 走 .vault/secrets.json::dayou.master_key (32 字节 base64)"
    )


def derive_user_key(user_id: str) -> bytes:
    """每用户独立 key · master + user_id 做 HKDF。"""
    salt = user_id.encode("utf-8")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"dayou-user-key-v1",
    )
    raw = hkdf.derive(_master_key())
    return base64.urlsafe_b64encode(raw)


def encrypt(user_id: str, plaintext: str) -> str:
    """返回 base64 字符串 · 存 sqlite text 字段。"""
    f = Fernet(derive_user_key(user_id))
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(user_id: str, ciphertext: str) -> str:
    f = Fernet(derive_user_key(user_id))
    return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")


def gen_master_key() -> str:
    """生成新 master key (CLI 一次性 · 老板初始化用)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


if __name__ == "__main__":
    print(gen_master_key())
