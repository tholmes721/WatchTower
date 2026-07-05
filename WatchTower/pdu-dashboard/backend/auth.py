"""
Authentication module — session-based auth with role-based access control.

Uses cookie-based sessions with signed tokens (no external dependencies
like JWT or Redis needed — works fully offline/airgapped).

Roles:
  - admin: full access (add/edit/delete PDUs, manage users)
  - viewer: read-only (dashboard, detail, trends, alerts — no modifications)
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import AsyncSessionLocal, User, get_db

# ── Configuration ────────────────────────────────────────────────────────────
# Secret key for signing session tokens. Generated once at startup if not set.
# In production, set via environment variable for persistence across restarts.
SESSION_SECRET = os.environ.get("WATCHTOWER_SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)

# Session lifetime: 24 hours (in seconds)
SESSION_LIFETIME = int(os.environ.get("WATCHTOWER_SESSION_LIFETIME", 86400))

# Cookie name
SESSION_COOKIE = "watchtower_session"


# ── Password hashing ─────────────────────────────────────────────────────────
# Uses PBKDF2-SHA256 with a random salt — no external dependencies required.
# This is built into Python's hashlib and suitable for password storage.

HASH_ITERATIONS = 260000  # OWASP recommended minimum for PBKDF2-SHA256


def hash_password(password: str) -> str:
    """Hash a password with a random salt using PBKDF2-SHA256."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), HASH_ITERATIONS)
    return f"pbkdf2:sha256:{HASH_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its stored hash."""
    try:
        parts = password_hash.split("$")
        if len(parts) != 3:
            return False
        header, salt, stored_hash = parts
        # Extract iterations from header
        _, _, iterations_str = header.split(":")
        iterations = int(iterations_str)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
        return hmac.compare_digest(dk.hex(), stored_hash)
    except (ValueError, AttributeError):
        return False


# ── Session token management ─────────────────────────────────────────────────
# Tokens are signed JSON payloads stored in a cookie.
# Format: base64(json payload) + "." + hmac signature

def _sign(payload: str) -> str:
    """Create an HMAC signature for a payload string."""
    return hmac.new(
        SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def create_session_token(user_id: int, username: str, role: str) -> str:
    """Create a signed session token."""
    payload = json.dumps({
        "user_id": user_id,
        "username": username,
        "role": role,
        "issued_at": int(time.time()),
    })
    # Simple hex encoding of payload + HMAC signature
    payload_hex = payload.encode().hex()
    signature = hmac.new(
        SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload_hex}.{signature}"


def verify_session_token(token: str) -> Optional[dict]:
    """Verify and decode a session token. Returns payload dict or None."""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_hex, signature = parts
        payload = bytes.fromhex(payload_hex).decode()
        # Verify signature
        expected_sig = hmac.new(
            SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        data = json.loads(payload)
        # Check expiry
        issued_at = data.get("issued_at", 0)
        if time.time() - issued_at > SESSION_LIFETIME:
            return None
        return data
    except (ValueError, json.JSONDecodeError):
        return None


# ── FastAPI dependencies ─────────────────────────────────────────────────────

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    """
    Extract and validate the session cookie.
    Returns the User object or None if not authenticated.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session_data = verify_session_token(token)
    if not session_data:
        return None
    # Look up user to ensure they still exist and role hasn't changed
    result = await db.execute(select(User).where(User.id == session_data["user_id"]))
    user = result.scalar_one_or_none()
    return user


async def require_auth(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Dependency that requires any authenticated user (admin or viewer)."""
    user = await get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Dependency that requires an admin user."""
    user = await get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Helper: create default admin on first run ────────────────────────────────

async def ensure_default_admin():
    """Create a default admin account if no users exist yet."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            return  # Users already exist

        default_admin = User(
            username="admin",
            password_hash=hash_password("watchtower"),
            role="admin",
            display_name="Administrator",
        )
        db.add(default_admin)
        await db.commit()
        print("")
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║  Default admin account created:                  ║")
        print("  ║    Username: admin                               ║")
        print("  ║    Password: watchtower                          ║")
        print("  ║                                                  ║")
        print("  ║  ⚠  Change this password after first login!     ║")
        print("  ╚══════════════════════════════════════════════════╝")
        print("")
