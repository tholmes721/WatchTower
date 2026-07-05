"""
Database migration script.
Safe to run multiple times — skips changes that already exist.

Migrations:
  1. Add discovered_* columns to pdu_configs
  2. Add exported_families_json to snapshots
  3. Create users table with default admin account
"""
import hashlib
import hmac
import secrets
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "pdu.db")

PDU_CONFIG_COLUMNS = [
    ("discovered_name",     "VARCHAR(128)"),
    ("discovered_model",    "VARCHAR(128)"),
    ("discovered_serial",   "VARCHAR(128)"),
    ("discovered_firmware", "VARCHAR(64)"),
]

SNAPSHOT_COLUMNS = [
    ("exported_families_json", "TEXT"),
]

USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(256) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'viewer',
    display_name VARCHAR(128),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME
)
"""

HASH_ITERATIONS = 260000


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 (matches backend/auth.py logic)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), HASH_ITERATIONS)
    return f"pbkdf2:sha256:{HASH_ITERATIONS}${salt}${dk.hex()}"


def add_columns(conn, table, new_columns):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    for col_name, col_type in new_columns:
        if col_name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
            print(f"  [{table}] Added column: {col_name}")
        else:
            print(f"  [{table}] Already exists: {col_name}")


def create_users_table(conn):
    cur = conn.cursor()
    # Check if table already exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if cur.fetchone():
        print("  [users] Table already exists")
        return False
    cur.execute(USERS_TABLE_SQL)
    print("  [users] Created table")
    return True


def ensure_default_admin(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    if count > 0:
        print("  [users] Users already exist — skipping default admin creation")
        return
    # Create default admin
    pw_hash = hash_password("watchtower")
    cur.execute(
        "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
        ("admin", pw_hash, "admin", "Administrator"),
    )
    print("")
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║  Default admin account created:                  ║")
    print("  ║    Username: admin                               ║")
    print("  ║    Password: watchtower                          ║")
    print("  ║                                                  ║")
    print("  ║  ⚠  Change this password after first login!     ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print("")


def migrate():
    if not os.path.exists(DB_PATH):
        print("No database found — will be created fresh on next app start.")
        return

    conn = sqlite3.connect(DB_PATH)

    print("Running migrations...")
    print()

    # Migration 1: pdu_configs columns
    add_columns(conn, "pdu_configs", PDU_CONFIG_COLUMNS)

    # Migration 2: snapshots columns
    add_columns(conn, "snapshots", SNAPSHOT_COLUMNS)

    # Migration 3: users table
    create_users_table(conn)
    ensure_default_admin(conn)

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
