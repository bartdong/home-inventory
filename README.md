# 🏠 家庭收纳可视化工具

一个交互式的家庭物品收纳管理工具，支持户型图可视化、柜子位置标注、物品搜索和操作记录回滚。

## 功能

- **户型图可视化** — SVG 户型图，房间/柜子位置一目了然
- **层级管理** — 房间 → 柜子 → 位置 → 物品，四级结构
- **即时搜索** — 输入关键词，高亮定位到具体柜子
- **物品属性** — 数量、位置、存放日期、保质期、标签、备注
- **批量操作** — 批量选择物品，一键移动
- **操作记录** — 最近 10 次操作备份，支持一键回滚
- **户型编辑** — 拖动调整房间/柜子位置和大小，网格吸附对齐
- **移动端适配** — 手机端自动切换为页面导航模式
- **导入导出** — JSON 格式，方便备份和迁移

## 文件结构

```
home-inventory/
├── index.html                  # 主页面
├── server.py                   # API 服务器（备份/回滚/数据保存）
├── home-inventory.json         # 实际数据（gitignore）
├── home-inventory.example.json # 示例数据
├── backups/                    # 操作备份（gitignore，最多 10 个）
├── .gitignore
└── README.md
```

## 启动

```bash
# 启动服务器（支持备份 API）
python3 server.py

# 或用普通 HTTP 服务器（仅浏览，不支持备份）
python3 -m http.server 80
```

访问 `http://localhost` 即可。

## 数据格式

`home-inventory.json` 结构：

```json
{
  "data": {
    "rooms": [
      {
        "id": "room_id",
        "name": "房间名",
        "icon": "🏠",
        "polygon": "x1,y1 x2,y2 x3,y3 x4,y4",
        "cabinets": [
          {
            "id": "cab_id",
            "name": "柜子名",
            "polygon": "x1,y1 x2,y2 x3,y3 x4,y4",
            "zones": [
              {
                "id": "zone_id",
                "name": "位置名",
                "items": [
                  {
                    "id": "item_id",
                    "name": "物品名",
                    "position": "左",
                    "tags": ["标签"],
                    "qty": 1,
                    "qtyTotal": 1
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  },
  "itemProps": {
    "item_id": {
      "dateStored": "2025-01-01",
      "expiry": null,
      "note": "备注"
    }
  }
}
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/backups` | GET | 获取操作记录列表 |
| `/api/backup/{idx}` | GET | 获取指定备份详情 |
| `/api/backup` | PUT | 创建备份 |
| `/api/data` | PUT | 保存数据 |
| `/api/rollback` | PUT | 回滚到指定备份 |

## License

MIT
