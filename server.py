#!/usr/bin/env python3
"""家庭收纳应用 — 后端服务"""
import os
import json
import secrets
import string
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, redirect, send_from_directory

# ── 配置 ──────────────────────────────────────────────
DATABASE_PATH = os.environ.get('DATABASE_PATH', 'data.db')
SESSION_SECRET = os.environ.get('SESSION_SECRET', secrets.token_hex(32))
SESSION_LIFETIME_MINUTES = int(os.environ.get('SESSION_LIFETIME_MINUTES', str(7*24*60)))  # 7 天免登录，活动续期
BACKEND_PORT = int(os.environ.get('BACKEND_PORT', '3002'))

ACCESS_CODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'access_code.txt')

def _load_access_code():
    """访问码：优先环境变量 ACCESS_CODE，其次 access_code.txt，都没有则生成并持久化"""
    code = os.environ.get('ACCESS_CODE', '').strip()
    if code:
        return code
    try:
        with open(ACCESS_CODE_FILE, 'r', encoding='utf-8') as f:
            code = f.read().strip()
            if code:
                return code
    except FileNotFoundError:
        pass
    chars = string.ascii_letters + '23456789'
    code = ''.join(secrets.choice(chars) for _ in range(8))
    with open(ACCESS_CODE_FILE, 'w', encoding='utf-8') as f:
        f.write(code)
    print(f'[access-code] 已生成新的访问码：{code}（写入 {ACCESS_CODE_FILE}）')
    return code

ACCESS_CODE = _load_access_code()

app = Flask(__name__)
app.secret_key = SESSION_SECRET


# ── 数据库工具 ────────────────────────────────────────
def get_db():
    """获取数据库连接（Row 模式，方便转 dict）"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def dict_row(row):
    """sqlite3.Row → dict"""
    return dict(row) if row else None


def dict_rows(rows):
    """[sqlite3.Row] → [dict]"""
    return [dict(r) for r in rows]


def ensure_schema():
    """兼容旧库：sessions 表可能缺 last_active 字段"""
    db = get_db()
    try:
        exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
        if not exists:
            return
        cols = [r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
        if 'last_active' not in cols:
            db.execute("ALTER TABLE sessions ADD COLUMN last_active TEXT")
            db.commit()
    finally:
        db.close()


# ── 认证中间件 ────────────────────────────────────────

def get_current_user():
    """从 session cookie 获取当前用户（5分钟不操作自动过期）"""
    session_id = request.cookies.get('session_id')
    if not session_id:
        return None
    db = get_db()
    try:
        row = db.execute(
            "SELECT s.user_id, s.expires_at, s.last_active, "
            "u.id, u.openid, u.nickname, u.role "
            "FROM sessions s JOIN users u ON s.user_id = u.id "
            "WHERE s.session_id = ?",
            (session_id,)
        ).fetchone()
        if not row:
            return None

        # 最长有效期检查
        expires_at = datetime.strptime(row['expires_at'], '%Y-%m-%d %H:%M:%S')
        if datetime.now() > expires_at:
            db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            db.commit()
            return None

        # 滑动窗口：有操作就续期（7 天免登录）
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        new_expires = (datetime.now() + timedelta(minutes=SESSION_LIFETIME_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute("UPDATE sessions SET last_active = ?, expires_at = ? WHERE session_id = ?", (now, new_expires, session_id))
        db.commit()

        return dict_row(row)
    finally:
        db.close()




def require_login(f):
    """要求已登录"""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.path.startswith('/home/api/'):
                return jsonify({'error': '未登录'}), 401
            return redirect('/home/login')
        request.user = user
        return f(*args, **kwargs)
    return decorated


def require_role(min_role):
    """要求角色等级 >= min_role"""
    hierarchy = {'user': 0, 'member': 1, 'admin': 2}
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({'error': '未登录'}), 401
            if hierarchy.get(user.get('role'), 0) < hierarchy.get(min_role, 0):
                return jsonify({'error': '权限不足'}), 403
            request.user = user
            return f(*args, **kwargs)
        return decorated
    return decorator


def create_session(db, user_id):
    """创建 session，返回 session_id（30 分钟滑动窗口）"""
    session_id = secrets.token_hex(32)
    expires_at = (datetime.now() + timedelta(minutes=SESSION_LIFETIME_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        "INSERT INTO sessions (session_id, user_id, expires_at, last_active) VALUES (?, ?, ?, ?)",
        (session_id, user_id, expires_at, now)
    )
    db.commit()
    return session_id, expires_at


# ── 健康检查 ──────────────────────────────────────────
@app.route('/home/api/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/home/login')
def login_page():
    """访问码登录页"""
    return send_from_directory('.', 'login.html')


@app.route('/home/auth/login', methods=['POST'])
def access_code_login():
    """访问码登录：校验通过即建 7 天 session"""
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    if not code or code != ACCESS_CODE:
        return jsonify({'error': '访问码错误'}), 401

    db = get_db()
    try:
        # 访问码登录固定映射到一个 owner 用户（不存在则自动创建 admin）
        user = db.execute("SELECT * FROM users WHERE openid = ?", ('__owner__',)).fetchone()
        if not user:
            cur = db.execute("INSERT INTO users (openid, nickname, role) VALUES (?, ?, ?)",
                             ('__owner__', '管理员', 'admin'))
            user_id = cur.lastrowid
        else:
            user_id = user['id']
        db.execute("UPDATE users SET last_login_at = datetime('now', 'localtime') WHERE id = ?", (user_id,))
        db.commit()

        session_id, expires_at = create_session(db, user_id)
        resp = jsonify({'ok': True})
        resp.set_cookie('session_id', session_id, httponly=True, samesite='Lax',
                        max_age=SESSION_LIFETIME_MINUTES * 60)
        return resp
    finally:
        db.close()


def cleanup_expired():
    """清理过期 session"""
    db = get_db()
    try:
        # session 通过 expires_at 自动过期，此处只做清理，无需其他逻辑
        db.execute("DELETE FROM sessions WHERE expires_at < datetime('now','localtime')")
        db.commit()
    finally:
        db.close()


@app.route('/home/auth/check')
def auth_check():
    """检查登录状态"""
    cleanup_expired()
    user = get_current_user()
    if user:
        return jsonify({
            'logged_in': True,
            'user': {
                'id': user['id'],
                'openid': user['openid'],
                'nickname': user['nickname'],
                'role': user['role']
            },
            'expires_at': user['expires_at']
        })
    return jsonify({'logged_in': False})


@app.route('/home/auth/logout', methods=['POST'])
def logout():
    """登出"""
    session_id = request.cookies.get('session_id')
    if session_id:
        db = get_db()
        try:
            db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            db.commit()
        finally:
            db.close()
    resp = jsonify({'ok': True})
    resp.delete_cookie('session_id')
    return resp




# ── 原站兼容 API（读写 home-inventory.json）────────────

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'home-inventory.json')

@app.route('/home/home-inventory.json')
@require_login
def serve_json():
    """原站：静态 JSON 文件（需登录，防止数据公开泄露）"""
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'application/json'}
    except FileNotFoundError:
        return '{}', 200, {'Content-Type': 'application/json'}

@app.route('/home/api/data', methods=['GET'])
@require_login
def get_data():
    """原站：加载数据"""
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except FileNotFoundError:
        return jsonify({'data': {'rooms': []}, 'itemProps': {}})

@app.route('/home/api/data', methods=['PUT'])
@require_role('admin')
def save_data():
    """原站：保存数据"""
    data = request.get_json()
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({'ok': True})

@app.route('/home/api/backup', methods=['PUT'])
@require_role('admin')
def create_backup():
    """原站：创建备份"""
    import shutil
    backup_dir = os.path.join(os.path.dirname(DATA_FILE), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(backup_dir, ts + '.json')
    shutil.copy2(DATA_FILE, dest)
    # 只保留最近 20 个备份
    backups = sorted(os.listdir(backup_dir))
    while len(backups) > 20:
        os.remove(os.path.join(backup_dir, backups.pop(0)))
    return jsonify({'ok': True, 'file': ts + '.json'})

@app.route('/home/api/backups', methods=['GET'])
@require_login
def list_backups():
    """原站：列出备份"""
    backup_dir = os.path.join(os.path.dirname(DATA_FILE), 'backups')
    if not os.path.exists(backup_dir):
        return jsonify([])
    files = sorted(os.listdir(backup_dir), reverse=True)
    return jsonify(files)

@app.route('/home/api/rollback', methods=['PUT'])
@require_role('admin')
def rollback():
    """原站：回滚备份"""
    import shutil
    data = request.get_json()
    filename = data.get('file', '')
    # 防路径遍历：只允许备份目录内的纯文件名
    if not filename or '/' in filename or '\\' in filename or filename.startswith('..'):
        return jsonify({'error': 'invalid filename'}), 400
    backup_dir = os.path.join(os.path.dirname(DATA_FILE), 'backups')
    src = os.path.join(backup_dir, filename)
    if not os.path.exists(src):
        return jsonify({'error': 'backup not found'}), 404
    shutil.copy2(src, DATA_FILE)
    # 返回回滚后的完整数据，前端据此刷新
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        new_data = json.load(f)
    return jsonify({'ok': True, 'data': new_data})

# ── 页面路由 ────────────────────────────────────────
@app.route('/home/')
@require_login
def index_page():
    """主页（需登录）"""
    return send_from_directory('.', 'index.html')


# ── 启动 ──────────────────────────────────────────────
if __name__ == '__main__':
    ensure_schema()
    print(f"🏠 家庭收纳后端启动 — port {BACKEND_PORT}")
    print(f"   DB: {DATABASE_PATH}")
    app.run(host='0.0.0.0', port=BACKEND_PORT, debug=True)
