"""
Email + password authentication. Each person creates their own account
(email + password they choose) - everyone then shares the same workspace
of projects, they just log in individually.

Passwords are hashed with PBKDF2-HMAC-SHA256 (100,000 iterations) + a
random salt per user, using only the Python standard library - no extra
dependency needed for something this security-sensitive.
"""
import hashlib
import os
import re
import binascii

import models

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return binascii.hexlify(salt).decode() + ":" + binascii.hexlify(pwd_hash).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split(":", 1)
        salt = binascii.unhexlify(salt_hex)
        pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
        return binascii.hexlify(pwd_hash).decode() == hash_hex
    except Exception:
        return False


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def password_issues(password: str) -> list[str]:
    """Returns a list of problems with the password, empty list = fine."""
    issues = []
    if len(password) < 8:
        issues.append("Password must be at least 8 characters.")
    if not re.search(r"[A-Za-z]", password):
        issues.append("Password must contain at least one letter.")
    if not re.search(r"[0-9]", password):
        issues.append("Password must contain at least one number.")
    return issues


def get_user_by_email(db, email: str):
    return db.query(models.User).filter(models.User.email == email.strip().lower()).first()


def create_user(db, email: str, password: str) -> models.User:
    user = models.User(email=email.strip().lower(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db, email: str, password: str):
    """Returns the User if email+password match, else None."""
    user = get_user_by_email(db, email)
    if user and verify_password(password, user.password_hash):
        return user
    return None
