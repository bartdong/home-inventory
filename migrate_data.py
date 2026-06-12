#!/usr/bin/env python3
"""迁移 home-inventory.json 到 SQLite"""
import json
import sqlite3
import sys
import os

DB_PATH = os.environ.get('DATABASE_PATH', 'data.db')
JSON_PATH = sys.argv[1] if len(sys.argv) > 1 else 'home-inventory.json'

with open(JSON_PATH) as f:
    raw = json.load(f)

data = raw.get('data', raw)
item_props = raw.get('itemProps', {})
rooms = data.get('rooms', [])

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")

# Bart is user id 1 (admin)
ADMIN_USER_ID = 1

cabinet_count = 0
item_count = 0

for room in rooms:
    room_name = room.get('name', '')
    for cab in room.get('cabinets', []):
        cab_name = cab.get('name', '')
        full_name = '%s · %s' % (room_name, cab_name)

        # 插入柜子
        cur = conn.execute(
            "INSERT INTO cabinets (name, location, description, created_by) VALUES (?, ?, ?, ?)",
            (cab_name, room_name, full_name, ADMIN_USER_ID)
        )
        cab_id = cur.lastrowid
        cabinet_count += 1

        # 插入柜子内所有物品（从各 zone 收集）
        for zone in cab.get('zones', []):
            zone_name = zone.get('name', '')
            for item in zone.get('items', []):
                item_name = item.get('name', '')
                qty = item.get('qty', 1)
                tags = item.get('tags', [])
                category = ', '.join(tags) if tags else ''
                position = '%s·%s' % (zone_name, item.get('position', '')) if zone_name else item.get('position', '')

                # 从 itemProps 补充信息
                props = item_props.get(item.get('id', ''), {})
                note = props.get('note', '')

                conn.execute(
                    "INSERT INTO items (cabinet_id, name, position, category, quantity, description, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cab_id, item_name, position, category, qty, note, ADMIN_USER_ID)
                )
                item_count += 1

conn.commit()
conn.close()

print('迁移完成：')
print('  柜子：%d' % cabinet_count)
print('  物品：%d' % item_count)
