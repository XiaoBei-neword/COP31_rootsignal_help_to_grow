"""SQLite 存储层：账号、地块档案、决策记录。所有查询强制带 user_id，保证账号间隔离。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS farm_profiles (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    crop_key           TEXT NOT NULL DEFAULT 'rice',
    location_name      TEXT NOT NULL DEFAULT '',
    latitude           REAL,
    longitude          REAL,
    area_mu            REAL NOT NULL DEFAULT 10,
    planting_date      TEXT,
    soil               TEXT NOT NULL DEFAULT 'loam',
    irrigation_method  TEXT NOT NULL DEFAULT 'sprinkler',
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS irrigation_records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    crop_key   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source     TEXT NOT NULL,
    decision   TEXT,
    net_mm     REAL,
    result     TEXT
);

CREATE TABLE IF NOT EXISTS harvest_plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    crop_key   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    window_start TEXT,
    window_end   TEXT,
    loss_saved_kg REAL,
    result     TEXT
);

CREATE INDEX IF NOT EXISTS idx_irr_user ON irrigation_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_harv_user ON harvest_plans(user_id, created_at DESC);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DB_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app) -> None:
    with app.app_context():
        conn = get_db()
        conn.executescript(SCHEMA)
        conn.commit()


def query(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return get_db().execute(sql, args).fetchall()


def query_one(sql: str, args: tuple = ()):
    return get_db().execute(sql, args).fetchone()


def execute(sql: str, args: tuple = ()) -> int:
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DEFAULT_PROFILE = {
    "crop_key": "rice",
    "location_name": "",
    "latitude": None,
    "longitude": None,
    "area_mu": 10.0,
    "planting_date": None,
    "soil": "loam",
    "irrigation_method": "sprinkler",
}


def ensure_profile(user_id: int) -> sqlite3.Row:
    row = query_one("SELECT * FROM farm_profiles WHERE user_id = ?", (user_id,))
    if row is None:
        execute(
            """INSERT INTO farm_profiles
               (user_id, crop_key, location_name, latitude, longitude, area_mu,
                planting_date, soil, irrigation_method, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                DEFAULT_PROFILE["crop_key"],
                DEFAULT_PROFILE["location_name"],
                DEFAULT_PROFILE["latitude"],
                DEFAULT_PROFILE["longitude"],
                DEFAULT_PROFILE["area_mu"],
                DEFAULT_PROFILE["planting_date"],
                DEFAULT_PROFILE["soil"],
                DEFAULT_PROFILE["irrigation_method"],
                now_text(),
            ),
        )
        row = query_one("SELECT * FROM farm_profiles WHERE user_id = ?", (user_id,))
    return row


def save_profile(user_id: int, **fields) -> None:
    allowed = {
        "crop_key", "location_name", "latitude", "longitude",
        "area_mu", "planting_date", "soil", "irrigation_method",
    }
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    data["updated_at"] = now_text()
    cols = ", ".join(f"{k} = ?" for k in data)
    execute(
        f"UPDATE farm_profiles SET {cols} WHERE user_id = ?",
        (*data.values(), user_id),
    )
