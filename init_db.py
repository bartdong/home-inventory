#!/usr/bin/env python3
"""初始化家庭收纳数据库"""
import os
import sqlite3

DB_PATH = os.environ.get('DATABASE_PATH', 'data.db')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 用户表
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        openid TEXT UNIQUE NOT NULL,
        nickname TEXT,
        role TEXT DEFAULT 'user',
        last_login_at TEXT
    )''')

    # 会话表
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        expires_at TEXT NOT NULL,
        last_active TEXT
    )''')
    # 兼容旧库：补充 last_active 字段
    cols = [r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
    if 'last_active' not in cols:
        c.execute("ALTER TABLE sessions ADD COLUMN last_active TEXT")

    conn.commit()
    conn.close()
    print(f"✅ 数据库初始化完成: {DB_PATH}")


if __name__ == '__main__':
    init_db()
