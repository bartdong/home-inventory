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
        unionid TEXT,
        nickname TEXT,
        avatar_url TEXT,
        role TEXT DEFAULT 'user',
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        last_login_at TEXT
    )''')

    # 柜子表
    c.execute('''CREATE TABLE IF NOT EXISTS cabinets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        location TEXT,
        description TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT
    )''')

    # 柜子-用户权限表
    c.execute('''CREATE TABLE IF NOT EXISTS cabinet_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cabinet_id INTEGER REFERENCES cabinets(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        permission_level TEXT DEFAULT 'user',
        granted_by INTEGER REFERENCES users(id),
        granted_at TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE(cabinet_id, user_id)
    )''')

    # 物品表
    c.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cabinet_id INTEGER REFERENCES cabinets(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        category TEXT,
        quantity INTEGER DEFAULT 1,
        position TEXT,
        description TEXT,
        tags TEXT,
        image_url TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT
    )''')

    # Magic Link 令牌表
    c.execute('''CREATE TABLE IF NOT EXISTS magic_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        openid TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        expires_at TEXT NOT NULL,
        used INTEGER DEFAULT 0
    )''')

    # 会话表
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        data TEXT,
        expires_at TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )''')

    conn.commit()
    conn.close()
    print(f"✅ 数据库初始化完成: {DB_PATH}")


if __name__ == '__main__':
    init_db()
