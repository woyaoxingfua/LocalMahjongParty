"""
events.py — Flask-SocketIO 事件处理器

职责：
  - 处理客户端 connect / disconnect 及所有游戏操作事件
  - 将解析后的参数转发给 room_manager / MahjongGame
  - 不包含任何游戏逻辑（逻辑在 game.py / logic.py）

注册方式：
  在 server.py 中调用 register_events(socketio) 完成注册
"""

from __future__ import annotations

from flask import request
from flask_socketio import SocketIO, emit, join_room

from room_manager import room_manager


def register_events(socketio: SocketIO) -> None:
    """将所有 SocketIO 事件绑定到给定的 socketio 实例"""

    # ── 连接 ───────────────────────────────────────────────────────
    @socketio.on('connect')
    def on_connect() -> None:
        sid = request.sid
        raw_pid = request.args.get('player_id')
        username = (request.args.get('username', '') or '').strip() or None

        # 尝试重连
        if raw_pid:
            try:
                pid = int(raw_pid)
                if room_manager.player_exists(pid):
                    room_manager.reconnect_player(pid, sid)
                    room_id = room_manager.get_room_id(pid)
                    if room_id:
                        game = room_manager.get_game(room_id)
                        if game:
                            join_room(room_id)
                            emit('player_info', {
                                'player_id': pid,
                                'username': room_manager.get_username(pid),
                                'reconnected': True,
                            })
                            game.broadcast_state()
                            socketio.emit('message', {
                                'text': f"{room_manager.get_username(pid)} 重新连接",
                                'type': 'info',
                            }, room=room_id)
                            return
            except (ValueError, TypeError):
                pass

        # 新玩家
        pid = room_manager.new_player(sid, username)
        uname = room_manager.get_username(pid)
        emit('player_info', {'player_id': pid, 'username': uname, 'reconnected': False})

        # 加入房间
        game = room_manager.find_open_room() or room_manager.make_room(socketio)
        game.add_player(pid)
        room_manager.set_room(pid, game.room_id)
        join_room(game.room_id)

        socketio.emit('message', {
            'text': f'{uname}（{game.seat_name(pid)}）加入房间',
            'type': 'join',
        }, room=game.room_id)
        socketio.emit('lobby_update', game.get_lobby_info(), room=game.room_id)

        if len(game.player_ids) == 4:
            socketio.emit('message', {'text': '四人已到齐！游戏开始！', 'type': 'system'}, room=game.room_id)
            game.start_game()

    # ── 断开 ───────────────────────────────────────────────────────
    @socketio.on('disconnect')
    def on_disconnect() -> None:
        sid = request.sid
        pid = room_manager.remove_sid(sid)
        if pid is None:
            return

        uname = room_manager.get_username(pid)
        room_id = room_manager.get_room_id(pid)

        room_manager.disconnect_player(pid)

        if not room_id:
            return

        game = room_manager.get_game(room_id)
        if not game:
            return

        socketio.emit('message', {
            'text': f'{uname} 已断开连接（可重连）',
            'type': 'warning',
        }, room=room_id)

        if game.phase == 'waiting':
            game.remove_player(pid)
            room_manager.delete_player(pid)
            socketio.emit('lobby_update', game.get_lobby_info(), room=room_id)
            if not game.player_ids:
                room_manager.remove_game(room_id)

    # ── 出牌 ───────────────────────────────────────────────────────
    @socketio.on('discard_tile')
    def on_discard(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_discard(pid, data.get('tile_code', ''))
        if not ok:
            emit('error', {'message': msg})

    # ── 碰/杠/胡/过 ────────────────────────────────────────────────
    @socketio.on('player_action')
    def on_action(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_action(pid, data.get('action', ''))
        if not ok:
            emit('error', {'message': msg})

    # ── 暗杠 ───────────────────────────────────────────────────────
    @socketio.on('angang')
    def on_angang(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_angang(pid, data.get('tile_code', ''))
        if not ok:
            emit('error', {'message': msg})

    # ── 补杠 ───────────────────────────────────────────────────────
    @socketio.on('bugang')
    def on_bugang(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_bugang(pid, data.get('tile_code', ''))
        if not ok:
            emit('error', {'message': msg})

    # ── 自摸 ───────────────────────────────────────────────────────
    @socketio.on('zimo')
    def on_zimo() -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_zimo(pid)
        if not ok:
            emit('error', {'message': msg})

    # ── 再来一局 ───────────────────────────────────────────────────
    @socketio.on('request_new_game')
    def on_new_game() -> None:
        pid, game = _resolve(request.sid)
        if game is None or game.phase != 'ended':
            return
        game.dealer_idx = (game.dealer_idx + 1) % len(game.player_ids)
        game.start_game()
        socketio.emit('message', {'text': '新一局开始！', 'type': 'system'}, room=game.room_id)


# ── 内部工具 ────────────────────────────────────────────────────────

def _resolve(sid: str):
    """根据 sid 查找 pid 和对应的 MahjongGame，任一不存在则返回 (None, None)"""
    pid = room_manager.get_pid_by_sid(sid)
    if pid is None:
        return None, None
    game = room_manager.get_game_by_pid(pid)
    return pid, game
