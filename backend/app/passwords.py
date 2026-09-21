"""
口令哈希：标准库 pbkdf2_hmac（sha256），不引入额外依赖。

存储格式（单行，便于直接写进 SQLite 一列）：
    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

校验用 hmac.compare_digest，避免时序侧信道。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGO = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 200_000
SALT_BYTES = 16

MIN_PASSWORD_LENGTH = 8


class PasswordError(ValueError):
    """口令不符合策略。"""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, iterations)
    return f"{ALGO}${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令。存储串损坏或格式不符时一律返回 False（不抛异常）。"""
    if not password or not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != ALGO:
        return False
    try:
        iterations = int(parts[1])
        salt = _b64d(parts[2])
        expected = _b64d(parts[3])
    except (ValueError, TypeError):
        return False
    try:
        actual = _derive(password, salt, iterations)
    except (ValueError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """老口令用了偏低的迭代次数时，登录成功后可以顺手升级。"""
    parts = (stored or "").split("$")
    if len(parts) != 4 or parts[0] != ALGO:
        return True
    try:
        return int(parts[1]) < iterations
    except ValueError:
        return True


def check_policy(password: str) -> None:
    """口令强度策略：长度 + 至少两类字符。不满足则抛 PasswordError。"""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise PasswordError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > 200:
        raise PasswordError("密码最长 200 位")
    kinds = sum(
        [
            any(c.islower() for c in password),
            any(c.upper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if kinds < 2:
        raise PasswordError("密码需包含字母、数字、符号中的至少两类")


def generate_password(length: int = 16) -> str:
    """生成一个符合策略的随机口令（不含易混淆字符）。"""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    symbols = "!@#$%^&*-_=+"
    while True:
        body = "".join(secrets.choice(alphabet) for _ in range(max(8, length - 2)))
        candidate = f"{body}{secrets.choice(symbols)}{secrets.choice('23456789')}"
        try:
            check_policy(candidate)
        except PasswordError:  # pragma: no cover - 理论上不会发生
            continue
        return candidate
