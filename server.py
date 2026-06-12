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
ADMIN_KEY = os.environ.get('ADMIN_KEY', '')
SESSION_SECRET = os.environ.get('SESSION_SECRET', secrets.token_hex(32))
SESSION_LIFETIME_DAYS = int(os.environ.get('SESSION_LIFETIME_DAYS', '30'))
MAGIC_LINK_EXPIRE_MINUTES = int(os.environ.get('MAGIC_LINK_EXPIRE_MINUTES', '5'))
BACKEND_PORT = int(os.environ.get('BACKEND_PORT', '3002'))

app = Flask(__name__)
app.secret_key = SESSION_SECRET


def generate_short_token():
    """生成 6 位短 token（去掉易混淆字符）"""
    chars = string.ascii_lowercase + string.ascii_uppercase + '23456789'
    while True:
        token = ''.join(secrets.choice(chars) for _ in range(6))
        # 唯一性检查
        db = get_db()
        try:
            exists = db.execute("SELECT 1 FROM magic_tokens WHERE token = ?", (token,)).fetchone()
            if not exists:
                return token
        finally:
            db.close()

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


# ── 认证中间件 ────────────────────────────────────────
SESSION_INACTIVITY_MINUTES = 5

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

        # 不活跃超时检查（5分钟没操作就过期）
        last_active = datetime.strptime(row['last_active'], '%Y-%m-%d %H:%M:%S')
        if (datetime.now() - last_active).total_seconds() > SESSION_INACTIVITY_MINUTES * 60:
            db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            db.commit()
            return None

        # 更新 last_active
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute("UPDATE sessions SET last_active = ? WHERE session_id = ?", (now, session_id))
        db.commit()

        return dict_row(row)
    finally:
        db.close()




def require_wechat(f):
    """要求微信内置浏览器访问"""
    @wraps(f)
    def decorated(*args, **kwargs):
        ua = request.headers.get('User-Agent', '')
        if 'MicroMessenger' not in ua:
            return redirect('/')
        return f(*args, **kwargs)
    return decorated

def require_login(f):
    """要求已登录"""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.path.startswith('/home/api/'):
                return jsonify({'error': '未登录'}), 401
            return redirect('/')
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


def require_admin_key(f):
    """管理接口：校验 X-Admin-Key"""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-Admin-Key', '')
        if not ADMIN_KEY or key != ADMIN_KEY:
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def create_session(db, user_id):
    """创建 session，返回 session_id"""
    session_id = secrets.token_hex(32)
    expires_at = (datetime.now() + timedelta(days=SESSION_LIFETIME_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
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


# ── 用户管理（管理接口）──────────────────────────────
@app.route('/home/api/admin/register-user', methods=['POST'])
@require_admin_key
def register_user():
    """注册新用户（管理接口）"""
    data = request.get_json()
    openid = data.get('openid')
    nickname = data.get('nickname', '')
    role = data.get('role', 'user')
    if not openid:
        return jsonify({'error': 'openid required'}), 400
    if role not in ('admin', 'member', 'user'):
        return jsonify({'error': 'invalid role'}), 400

    db = get_db()
    try:
        existing = db.execute("SELECT id FROM users WHERE openid = ?", (openid,)).fetchone()
        if existing:
            return jsonify({'error': 'user already exists', 'id': existing['id']}), 409
        cur = db.execute(
            "INSERT INTO users (openid, nickname, role) VALUES (?, ?, ?)",
            (openid, nickname, role)
        )
        db.commit()
        return jsonify({'ok': True, 'id': cur.lastrowid, 'openid': openid, 'role': role})
    finally:
        db.close()


@app.route('/home/api/admin/magic-link', methods=['POST'])
@require_admin_key
def generate_magic_link():
    """生成 Magic Link（管理接口）"""
    data = request.get_json()
    openid = data.get('openid')
    if not openid:
        return jsonify({'error': 'openid required'}), 400

    db = get_db()
    try:
        user = db.execute("SELECT id, nickname FROM users WHERE openid = ?", (openid,)).fetchone()
        if not user:
            # 自动注册：小柔生成链接时直接关联用户
            nickname = data.get('nickname', '')
            role = data.get('role', 'user')
            cur = db.execute(
                "INSERT INTO users (openid, nickname, role) VALUES (?, ?, ?)",
                (openid, nickname, role)
            )
            user_id = cur.lastrowid
            user_name = nickname
        else:
            user_id = user['id']
            user_name = user['nickname']

        token = generate_short_token()
        expires_at = (datetime.now() + timedelta(minutes=MAGIC_LINK_EXPIRE_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute(
            "INSERT INTO magic_tokens (token, openid, expires_at) VALUES (?, ?, ?)",
            (token, openid, expires_at)
        )
        db.commit()

        domain = os.environ.get('DOMAIN', 'localhost:3002')
        scheme = 'https' if domain != 'localhost:3002' else 'http'
        url = f"{scheme}://{domain}/home/auth/magic-login?token={token}"

        return jsonify({
            'ok': True,
            'token': token,
            'url': url,
            'expire_at': expires_at,
            'user_name': user_name
        })
    finally:
        db.close()


# ── 认证路由 ──────────────────────────────────────────
@app.route('/home/auth/magic-login')
def magic_login():
    """Magic Link 登录验证"""
    token = request.args.get('token', '')
    if not token:
        return jsonify({'error': 'token required'}), 400

    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM magic_tokens WHERE token = ? ",
            (token,)
        ).fetchone()

        if not row:
            return redirect('/')

        # 检查过期
        expires_at = datetime.strptime(row['expires_at'], '%Y-%m-%d %H:%M:%S')
        if datetime.now() > expires_at:
            return redirect('/')

        # 检查是否已使用
        if row['used']:
            return redirect('/')

        # 查找用户
        user = db.execute("SELECT * FROM users WHERE openid = ?", (row['openid'],)).fetchone()
        if not user:
            return redirect('/')

        # 标记 token 已使用
        db.execute("UPDATE magic_tokens SET used = 1 WHERE id = ?", (row['id'],))

        # 创建 session
        session_id, _ = create_session(db, user['id'])

        # 更新最后登录时间
        db.execute(
            "UPDATE users SET last_login_at = datetime('now', 'localtime') WHERE id = ?",
            (user['id'],)
        )
        db.commit()

        resp = redirect('/home/')
        resp.set_cookie('session_id', session_id, httponly=True, samesite='Lax',
                        max_age=SESSION_LIFETIME_DAYS * 86400)
        return resp
    finally:
        db.close()


def cleanup_expired():
    """清理过期 session 和 token，同一用户只保留最新 session"""
    db = get_db()
    try:
        db.execute("DELETE FROM magic_tokens WHERE expires_at < datetime('now','localtime')")
        db.execute("""DELETE FROM sessions WHERE session_id NOT IN (
            SELECT session_id FROM (
                SELECT session_id, user_id,
                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at DESC) as rn
                FROM sessions
            ) WHERE rn = 1
        )""")
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
            }
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
def serve_json():
    """原站：静态 JSON 文件"""
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
    backup_dir = os.path.join(os.path.dirname(DATA_FILE), 'backups')
    src = os.path.join(backup_dir, filename)
    if not os.path.exists(src):
        return jsonify({'error': 'backup not found'}), 404
    shutil.copy2(src, DATA_FILE)
    return jsonify({'ok': True})

# ── 页面路由 ────────────────────────────────────────
@app.route('/home/')
@require_login
def index_page():
    """主页（需登录）"""
    return send_from_directory('.', 'index.html')


# ── 用户列表（admin）─────────────────────────────────
@app.route('/home/api/users')
@require_role('admin')
def list_users():
    """获取用户列表"""
    db = get_db()
    try:
        rows = db.execute("SELECT id, openid, nickname, role, is_active, created_at, last_login_at FROM users").fetchall()
        return jsonify({'users': dict_rows(rows)})
    finally:
        db.close()


@app.route('/home/api/users/<int:user_id>/role', methods=['PUT'])
@require_role('admin')
def update_user_role(user_id):
    """修改用户角色"""
    data = request.get_json()
    new_role = data.get('role')
    if new_role not in ('admin', 'member', 'user'):
        return jsonify({'error': 'invalid role'}), 400

    db = get_db()
    try:
        db.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ── 柜子管理 ────────────────────────────────────────
@app.route('/home/api/cabinets')
@require_login
def list_cabinets():
    """获取柜子列表（admin 看所有，其他用户只看有权限的）"""
    user = request.user
    db = get_db()
    try:
        if user['role'] == 'admin':
            rows = db.execute(
                "SELECT c.*, u.nickname AS created_by_name "
                "FROM cabinets c LEFT JOIN users u ON c.created_by = u.id "
                "ORDER BY c.created_at DESC"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT c.*, u.nickname AS created_by_name "
                "FROM cabinets c "
                "LEFT JOIN users u ON c.created_by = u.id "
                "LEFT JOIN cabinet_permissions cp ON c.id = cp.cabinet_id "
                "WHERE cp.user_id = ? OR c.created_by = ? "
                "GROUP BY c.id ORDER BY c.created_at DESC",
                (user['id'], user['id'])
            ).fetchall()
        return jsonify({'cabinets': dict_rows(rows)})
    finally:
        db.close()


@app.route('/home/api/cabinets', methods=['POST'])
@require_role('admin')
def create_cabinet():
    """创建柜子"""
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400

    location = data.get('location', '').strip()
    description = data.get('description', '').strip()

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO cabinets (name, location, description, created_by) VALUES (?, ?, ?, ?)",
            (name, location, description, request.user['id'])
        )
        db.commit()
        return jsonify({'ok': True, 'id': cur.lastrowid, 'name': name})
    finally:
        db.close()


def check_cabinet_access(db, cabinet_id, user):
    """检查用户对某柜子的访问权限，返回 (has_access, permission_level)"""
    if user['role'] == 'admin':
        return True, 'admin'
    row = db.execute(
        "SELECT permission_level FROM cabinet_permissions "
        "WHERE cabinet_id = ? AND user_id = ?",
        (cabinet_id, user['id'])
    ).fetchone()
    if row:
        return True, row['permission_level']
    # 检查是否是柜子创建者
    cabinet = db.execute("SELECT created_by FROM cabinets WHERE id = ?", (cabinet_id,)).fetchone()
    if cabinet and cabinet['created_by'] == user['id']:
        return True, 'admin'
    return False, None


@app.route('/home/api/cabinets/<int:cabinet_id>')
@require_login
def get_cabinet(cabinet_id):
    """获取柜子详情"""
    db = get_db()
    try:
        has_access, perm = check_cabinet_access(db, cabinet_id, request.user)
        if not has_access:
            return jsonify({'error': '无权访问此柜子'}), 403

        cabinet = db.execute(
            "SELECT c.*, u.nickname AS created_by_name "
            "FROM cabinets c LEFT JOIN users u ON c.created_by = u.id "
            "WHERE c.id = ?",
            (cabinet_id,)
        ).fetchone()
        if not cabinet:
            return jsonify({'error': 'cabinet not found'}), 404

        result = dict_row(cabinet)
        result['my_permission'] = perm

        # 加载柜子内物品
        items = db.execute(
            "SELECT * FROM items WHERE cabinet_id = ? ORDER BY position, name",
            (cabinet_id,)
        ).fetchall()
        result['items'] = dict_rows(items)

        return jsonify(result)
    finally:
        db.close()


@app.route('/home/api/cabinets/<int:cabinet_id>', methods=['PUT'])
@require_role('admin')
def update_cabinet(cabinet_id):
    """修改柜子"""
    data = request.get_json()
    db = get_db()
    try:
        cabinet = db.execute("SELECT id FROM cabinets WHERE id = ?", (cabinet_id,)).fetchone()
        if not cabinet:
            return jsonify({'error': 'cabinet not found'}), 404

        fields = []
        params = []
        for key in ('name', 'location', 'description'):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return jsonify({'error': 'no fields to update'}), 400

        fields.append("updated_at = datetime('now', 'localtime')")
        params.append(cabinet_id)
        db.execute(f"UPDATE cabinets SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@app.route('/home/api/cabinets/<int:cabinet_id>', methods=['DELETE'])
@require_role('admin')
def delete_cabinet(cabinet_id):
    """删除柜子（级联删除物品和权限）"""
    db = get_db()
    try:
        cabinet = db.execute("SELECT id FROM cabinets WHERE id = ?", (cabinet_id,)).fetchone()
        if not cabinet:
            return jsonify({'error': 'cabinet not found'}), 404
        db.execute("DELETE FROM cabinets WHERE id = ?", (cabinet_id,))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ── 柜子权限管理 ────────────────────────────────────
@app.route('/home/api/cabinets/<int:cabinet_id>/permissions')
@require_role('admin')
def list_cabinet_permissions(cabinet_id):
    """查看柜子权限"""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT cp.*, u.nickname, u.openid "
            "FROM cabinet_permissions cp "
            "JOIN users u ON cp.user_id = u.id "
            "WHERE cp.cabinet_id = ?",
            (cabinet_id,)
        ).fetchall()
        return jsonify({'permissions': dict_rows(rows)})
    finally:
        db.close()


@app.route('/home/api/cabinets/<int:cabinet_id>/permissions', methods=['POST'])
@require_role('admin')
def set_cabinet_permission(cabinet_id):
    """设置柜子权限（覆盖式）"""
    data = request.get_json()
    user_id = data.get('user_id')
    level = data.get('permission_level', 'user')

    if not user_id:
        return jsonify({'error': 'user_id required'}), 400
    if level not in ('admin', 'member', 'user'):
        return jsonify({'error': 'invalid permission_level'}), 400

    db = get_db()
    try:
        # 检查柜子存在
        cabinet = db.execute("SELECT id FROM cabinets WHERE id = ?", (cabinet_id,)).fetchone()
        if not cabinet:
            return jsonify({'error': 'cabinet not found'}), 404

        # 检查用户存在
        user = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({'error': 'user not found'}), 404

        # UPSERT
        db.execute(
            "INSERT INTO cabinet_permissions (cabinet_id, user_id, permission_level, granted_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(cabinet_id, user_id) DO UPDATE SET permission_level = ?",
            (cabinet_id, user_id, level, request.user['id'], level)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@app.route('/home/api/cabinets/<int:cabinet_id>/permissions/<int:user_id>', methods=['DELETE'])
@require_role('admin')
def remove_cabinet_permission(cabinet_id, user_id):
    """移除柜子权限"""
    db = get_db()
    try:
        db.execute(
            "DELETE FROM cabinet_permissions WHERE cabinet_id = ? AND user_id = ?",
            (cabinet_id, user_id)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ── 物品管理 ────────────────────────────────────────
@app.route('/home/api/cabinets/<int:cabinet_id>/items')
@require_login
def list_items(cabinet_id):
    """获取柜子中的物品"""
    db = get_db()
    try:
        has_access, _ = check_cabinet_access(db, cabinet_id, request.user)
        if not has_access:
            return jsonify({'error': '无权访问此柜子'}), 403

        rows = db.execute(
            "SELECT i.*, u.nickname AS created_by_name "
            "FROM items i LEFT JOIN users u ON i.created_by = u.id "
            "WHERE i.cabinet_id = ? ORDER BY i.created_at DESC",
            (cabinet_id,)
        ).fetchall()
        return jsonify({'items': dict_rows(rows)})
    finally:
        db.close()


@app.route('/home/api/cabinets/<int:cabinet_id>/items', methods=['POST'])
@require_role('member')
def create_item(cabinet_id):
    """添加物品（admin/member）"""
    db = get_db()
    try:
        has_access, _ = check_cabinet_access(db, cabinet_id, request.user)
        if not has_access:
            return jsonify({'error': '无权访问此柜子'}), 403

        data = request.get_json()
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'error': 'name required'}), 400

        cur = db.execute(
            "INSERT INTO items (cabinet_id, name, category, quantity, position, description, tags, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cabinet_id,
                name,
                data.get('category', ''),
                data.get('quantity', 1),
                data.get('position', ''),
                data.get('description', ''),
                data.get('tags', ''),
                request.user['id'],
            )
        )
        db.commit()
        return jsonify({'ok': True, 'id': cur.lastrowid, 'name': name})
    finally:
        db.close()


@app.route('/home/api/items/<int:item_id>', methods=['PUT'])
@require_role('member')
def update_item(item_id):
    """修改物品（admin/member）"""
    db = get_db()
    try:
        item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({'error': 'item not found'}), 404

        has_access, _ = check_cabinet_access(db, item['cabinet_id'], request.user)
        if not has_access:
            return jsonify({'error': '无权访问此柜子'}), 403

        data = request.get_json()
        fields = []
        params = []
        for key in ('name', 'category', 'quantity', 'position', 'description', 'tags'):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return jsonify({'error': 'no fields to update'}), 400

        fields.append("updated_at = datetime('now', 'localtime')")
        params.append(item_id)
        db.execute(f"UPDATE items SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@app.route('/home/api/items/<int:item_id>', methods=['DELETE'])
@require_role('member')
def delete_item(item_id):
    """删除物品（admin/member）"""
    db = get_db()
    try:
        item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({'error': 'item not found'}), 404

        has_access, _ = check_cabinet_access(db, item['cabinet_id'], request.user)
        if not has_access:
            return jsonify({'error': '无权访问此柜子'}), 403

        db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@app.route('/home/api/search')
@require_login
def search_items():
    """搜索物品（仅搜用户有权限的柜子）"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'items': []})

    db = get_db()
    try:
        user = request.user
        if user['role'] == 'admin':
            rows = db.execute(
                "SELECT i.*, c.name AS cabinet_name, c.location AS cabinet_location "
                "FROM items i JOIN cabinets c ON i.cabinet_id = c.id "
                "WHERE i.name LIKE ? OR i.category LIKE ? OR i.description LIKE ? OR i.tags LIKE ? "
                "ORDER BY i.updated_at DESC",
                (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT i.*, c.name AS cabinet_name, c.location AS cabinet_location "
                "FROM items i JOIN cabinets c ON i.cabinet_id = c.id "
                "LEFT JOIN cabinet_permissions cp ON c.id = cp.cabinet_id AND cp.user_id = ? "
                "WHERE (cp.user_id IS NOT NULL OR c.created_by = ?) "
                "AND (i.name LIKE ? OR i.category LIKE ? OR i.description LIKE ? OR i.tags LIKE ?) "
                "ORDER BY i.updated_at DESC",
                (user['id'], user['id'], f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')
            ).fetchall()
        return jsonify({'items': dict_rows(rows)})
    finally:
        db.close()


# ── 启动 ──────────────────────────────────────────────
if __name__ == '__main__':
    print(f"🏠 家庭收纳后端启动 — port {BACKEND_PORT}")
    print(f"   DB: {DATABASE_PATH}")
    app.run(host='0.0.0.0', port=BACKEND_PORT, debug=True)
