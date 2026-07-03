"""
One-time migration: add discovered_* columns to pdu_configs and
exported_families_json to snapshots.
Safe to run multiple times (skips columns that already exist).
"""
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

def migrate():
    if not os.path.exists(DB_PATH):
        print("No database found — will be created fresh on next app start.")
        return

    conn = sqlite3.connect(DB_PATH)
    add_columns(conn, "pdu_configs", PDU_CONFIG_COLUMNS)
    add_columns(conn, "snapshots",   SNAPSHOT_COLUMNS)
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
