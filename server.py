#!/usr/bin/env python3
"""Simple API server for home-inventory with backup support."""
import http.server
import json
import os
import glob
from datetime import datetime

PORT = 3002
DATA_FILE = 'home-inventory.json'
BACKUP_DIR = 'backups'
MAX_BACKUPS = 10

os.makedirs(BACKUP_DIR, exist_ok=True)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/backups':
            self.send_json(self.list_backups())
        elif self.path.startswith('/api/backup/'):
            idx = self.path.split('/')[-1]
            self.send_json(self.get_backup(idx))
        else:
            super().do_GET()

    def do_PUT(self):
        if self.path == '/api/data':
            self.handle_save_data()
        elif self.path == '/api/backup':
            self.handle_create_backup()
        elif self.path == '/api/rollback':
            self.handle_rollback()
        else:
            self.send_error(404)

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def list_backups(self):
        files = sorted(glob.glob(f'{BACKUP_DIR}/*.json'), reverse=True)[:MAX_BACKUPS]
        backups = []
        for f in files:
            try:
                with open(f) as fp:
                    meta = json.load(fp)
                backups.append({
                    'file': os.path.basename(f),
                    'time': meta.get('time', ''),
                    'summary': meta.get('summary', ''),
                })
            except:
                pass
        return backups

    def get_backup(self, idx):
        files = sorted(glob.glob(f'{BACKUP_DIR}/*.json'), reverse=True)
        try:
            with open(files[int(idx)]) as fp:
                return json.load(fp)
        except:
            return {'error': 'not found'}

    def handle_save_data(self):
        body = self.read_body()
        with open(DATA_FILE, 'w') as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        self.send_json({'ok': True})

    def handle_create_backup(self):
        body = self.read_body()
        summary = body.get('summary', '未知操作')
        # Read current data
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
        except:
            data = {}
        # Save backup
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = f'{BACKUP_DIR}/{ts}.json'
        with open(backup_file, 'w') as f:
            json.dump({
                'time': datetime.now().isoformat(),
                'summary': summary,
                'snapshot': data,
            }, f, ensure_ascii=False, indent=2)
        # Cleanup old backups
        files = sorted(glob.glob(f'{BACKUP_DIR}/*.json'))
        while len(files) > MAX_BACKUPS:
            os.remove(files.pop(0))
        self.send_json({'ok': True, 'file': backup_file})

    def handle_rollback(self):
        body = self.read_body()
        backup_file = body.get('file', '')
        path = f'{BACKUP_DIR}/{backup_file}'
        if not os.path.exists(path):
            self.send_json({'error': 'backup not found'}, 404)
            return
        with open(path) as f:
            backup = json.load(f)
        snapshot = backup.get('snapshot', {})
        # Restore data
        with open(DATA_FILE, 'w') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        # Remove this and newer backups
        files = sorted(glob.glob(f'{BACKUP_DIR}/*.json'), reverse=True)
        target_idx = files.index(path) if path in files else -1
        if target_idx >= 0:
            for f in files[:target_idx + 1]:
                os.remove(f)
        self.send_json({'ok': True, 'data': snapshot})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

if __name__ == '__main__':
    print(f'Starting server on port {PORT}...')
    http.server.HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
