"""
server.py — 应用入口

职责（仅此而已）：
  - 创建 Flask app 与 SocketIO 实例
  - 注册 HTTP 路由（只有 /）
  - 调用 events.register_events() 绑定 SocketIO 事件
  - 启动服务器
"""

import socket as _socket
import os
import json
import glob

import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, jsonify, send_file, request
from flask_socketio import SocketIO

from events import register_events

# ── Flask & SocketIO ────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'mahjong-lan-secret-v2'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')

# ── 路由 ────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

# ── 回放路由 ──────────────────────────────────────────────

REPLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replays')


@app.route('/replays')
def list_replays():
    """返回所有回放文件列表（按时间倒序）"""
    if not os.path.isdir(REPLAY_DIR):
        return jsonify([])
    files = glob.glob(os.path.join(REPLAY_DIR, '*.json'))
    replays = []
    for f in sorted(files, key=os.path.getmtime, reverse=True):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            replays.append({
                'game_id': data.get('game_id', ''),
                'start_time': data.get('start_time', ''),
                'players': data.get('players', []),
                'result': data.get('result'),
                'action_count': len(data.get('actions', [])),
            })
        except Exception:
            continue
    return jsonify(replays)


@app.route('/replay/<game_id>')
def get_replay(game_id: str):
    """返回指定回放数据"""
    # 安全：仅允许合法文件名
    safe_id = game_id.replace('/', '').replace('\\', '').replace('..', '')
    filepath = os.path.join(REPLAY_DIR, f'{safe_id}.json')
    if not os.path.isfile(filepath):
        return jsonify({'error': '回放不存在'}), 404
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'读取失败: {e}'}), 500


@app.route('/replay/<game_id>/download')
def download_replay(game_id: str):
    """下载回放 JSON 文件"""
    safe_id = game_id.replace('/', '').replace('\\', '').replace('..', '')
    filepath = os.path.join(REPLAY_DIR, f'{safe_id}.json')
    if not os.path.isfile(filepath):
        return jsonify({'error': '回放不存在'}), 404
    return send_file(filepath, as_attachment=True, download_name=f'{safe_id}.json')

# ── 事件注册 ────────────────────────────────────────────────────
register_events(socketio)

# ── 启动 ────────────────────────────────────────────────────────

def _get_local_ip() -> str:
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


if __name__ == '__main__':
    ip = _get_local_ip()
    port = 5000
    print('=' * 50)
    print('  麻将对战服务器已启动')
    print(f'  局域网地址: http://{ip}:{port}')
    print(f'  本地地址:   http://localhost:{port}')
    print('=' * 50)
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
