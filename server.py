"""
server.py — 应用入口

职责（仅此而已）：
  - 创建 Flask app 与 SocketIO 实例
  - 注册 HTTP 路由（只有 /）
  - 调用 events.register_events() 绑定 SocketIO 事件
  - 启动服务器
"""

import socket as _socket

import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template
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
