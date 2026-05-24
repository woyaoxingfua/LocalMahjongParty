"""
events.py — Flask-SocketIO 事件处理器

职责：
  - 处理客户端 connect / disconnect 及所有游戏操作事件
  - 将解析后的参数转发给 room_manager / MahjongGame
  - 不包含任何游戏逻辑（逻辑在 game.py / logic.py）
  - 新增：房间系统事件、聊天事件、观战事件

注册方式：
  在 server.py 中调用 register_events(socketio) 完成注册
"""

from __future__ import annotations

from flask import request
from flask_socketio import SocketIO, emit, join_room, leave_room

from room_manager import room_manager


def _broadcast_room_list(socketio: SocketIO) -> None:
    """广播房间列表更新到所有已连接的客户端（通过全局事件）"""
    rooms = room_manager.list_rooms()
    socketio.emit('room_list_update', {'rooms': rooms})


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
                            # 判断是观战者还是玩家
                            is_spec = room_manager.is_spectator(pid, room_id)
                            join_room(room_id)
                            emit('player_info', {
                                'player_id': pid,
                                'username': room_manager.get_username(pid),
                                'reconnected': True,
                                'is_spectator': is_spec,
                                'room_id': room_id,
                            })
                            if is_spec:
                                game.broadcast_state_to_spectators([pid])
                            else:
                                game.broadcast_state()
                            socketio.emit('message', {
                                'text': f"{room_manager.get_username(pid)} 重新连接",
                                'type': 'info',
                            }, room=room_id)
                            return
                    else:
                        # 重连但没有房间了，回大厅
                        join_room('lobby')
                        emit('player_info', {
                            'player_id': pid,
                            'username': room_manager.get_username(pid),
                            'reconnected': True,
                            'room_id': None,
                        })
                        emit('room_list_update', {'rooms': room_manager.list_rooms()})
                        return
            except (ValueError, TypeError):
                pass

        # 新玩家：不自动加入房间，进入大厅
        pid = room_manager.new_player(sid, username)
        uname = room_manager.get_username(pid)
        join_room('lobby')
        emit('player_info', {
            'player_id': pid,
            'username': uname,
            'reconnected': False,
            'room_id': None,
        })
        # 发送当前房间列表
        emit('room_list_update', {'rooms': room_manager.list_rooms()})

    # ── 断开 ───────────────────────────────────────────────────────
    @socketio.on('disconnect')
    def on_disconnect() -> None:
        sid = request.sid
        pid = room_manager.remove_sid(sid)
        if pid is None:
            return

        uname = room_manager.get_username(pid)
        room_id = room_manager.get_room_id(pid)

        # 如果是观战者，直接移除
        if room_id and room_manager.is_spectator(pid, room_id):
            room_manager.remove_spectator(room_id, pid)
            room_manager.delete_player(pid)
            socketio.emit('message', {
                'text': f'旁观者 {uname} 离开了',
                'type': 'info',
            }, room=room_id)
            _broadcast_room_list(socketio)
            return

        room_manager.disconnect_player(pid)

        if not room_id:
            return

        game = room_manager.get_game(room_id)
        if not game:
            return

        socketio.emit('message', {
            'text': f'{uname} 已断开连接（可重连，AI自动托管）',
            'type': 'warning',
        }, room=room_id)

        if game.phase == 'waiting':
            game.remove_player(pid)
            room_manager.delete_player(pid)
            socketio.emit('lobby_update', game.get_lobby_info(), room=room_id)
            _broadcast_room_list(socketio)
            if not game.player_ids:
                room_manager.remove_game(room_id)
        elif game.phase in ('discard_wait', 'action_wait'):
            # 游戏进行中，AI 托管
            spectator_pids = list(room_manager.get_spectators(room_id))
            game.trigger_ai_if_needed(spectator_pids)

        _broadcast_room_list(socketio)

    # ── 列出房间 ──────────────────────────────────────────────────
    @socketio.on('list_rooms')
    def on_list_rooms() -> None:
        emit('room_list_update', {'rooms': room_manager.list_rooms()})

    # ── 创建房间 ──────────────────────────────────────────────────
    @socketio.on('create_room')
    def on_create_room(data: dict) -> None:
        pid, _ = _resolve(request.sid)
        if pid is None:
            emit('error', {'message': '未登录'})
            return

        # 如果已在某个房间，先离开
        old_room = room_manager.get_room_id(pid)
        if old_room:
            emit('error', {'message': '你已在房间中，请先离开当前房间'})
            return

        room_name = (data.get('room_name', '') or '').strip() or '新房间'
        game = room_manager.make_room(socketio, room_name=room_name, owner_pid=pid)
        game.add_player(pid)
        room_manager.set_room(pid, game.room_id)
        leave_room('lobby')
        join_room(game.room_id)

        uname = room_manager.get_username(pid)
        socketio.emit('message', {
            'text': f'{uname}（{game.seat_name(pid)}）创建了房间「{room_name}」',
            'type': 'join',
        }, room=game.room_id)
        socketio.emit('lobby_update', game.get_lobby_info(), room=game.room_id)

        emit('joined_room', {
            'room_id': game.room_id,
            'room_name': room_name,
            'is_owner': True,
        })

        _broadcast_room_list(socketio)

    # ── 加入房间 ──────────────────────────────────────────────────
    @socketio.on('join_room')
    def on_join_room(data: dict) -> None:
        pid, _ = _resolve(request.sid)
        if pid is None:
            emit('error', {'message': '未登录'})
            return

        # 如果已在某个房间
        old_room = room_manager.get_room_id(pid)
        if old_room:
            emit('error', {'message': '你已在房间中，请先离开当前房间'})
            return

        room_id = data.get('room_id', '')
        game = room_manager.find_room_by_id(room_id)
        if not game:
            emit('error', {'message': '房间不存在'})
            return

        if game.phase != 'waiting':
            emit('error', {'message': '游戏已开始，无法加入'})
            return

        if len(game.player_ids) >= 4:
            emit('error', {'message': '房间已满'})
            return

        game.add_player(pid)
        room_manager.set_room(pid, game.room_id)
        leave_room('lobby')
        join_room(game.room_id)

        uname = room_manager.get_username(pid)
        is_owner = (room_manager.get_room_owner(game.room_id) == pid)
        socketio.emit('message', {
            'text': f'{uname}（{game.seat_name(pid)}）加入房间',
            'type': 'join',
        }, room=game.room_id)
        socketio.emit('lobby_update', game.get_lobby_info(), room=game.room_id)

        emit('joined_room', {
            'room_id': game.room_id,
            'room_name': room_manager.get_room_name(game.room_id),
            'is_owner': is_owner,
        })

        _broadcast_room_list(socketio)

    # ── 离开房间（等待中） ────────────────────────────────────────
    @socketio.on('leave_room')
    def on_leave_room() -> None:
        pid, _ = _resolve(request.sid)
        if pid is None:
            return

        room_id = room_manager.get_room_id(pid)
        if not room_id:
            return

        # 如果是观战者
        if room_manager.is_spectator(pid, room_id):
            room_manager.remove_spectator(room_id, pid)
            room_manager.set_room(pid, None)
            leave_room(room_id)
            join_room('lobby')
            uname = room_manager.get_username(pid)
            socketio.emit('message', {
                'text': f'旁观者 {uname} 离开了',
                'type': 'info',
            }, room=room_id)
            emit('left_room', {})
            _broadcast_room_list(socketio)
            return

        game = room_manager.get_game(room_id)
        if not game:
            return

        if game.phase != 'waiting':
            emit('error', {'message': '游戏进行中，无法离开房间'})
            return

        uname = room_manager.get_username(pid)
        game.remove_player(pid)
        room_manager.set_room(pid, None)
        room_manager.delete_player(pid)
        leave_room(room_id)
        join_room('lobby')

        socketio.emit('message', {
            'text': f'{uname} 离开了房间',
            'type': 'info',
        }, room=room_id)
        socketio.emit('lobby_update', game.get_lobby_info(), room=room_id)

        emit('left_room', {})

        if not game.player_ids:
            room_manager.remove_game(room_id)

        _broadcast_room_list(socketio)

    # ── 开始游戏（房主操作） ───────────────────────────────────────
    @socketio.on('start_game')
    def on_start_game() -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return

        room_id = game.room_id
        owner_pid = room_manager.get_room_owner(room_id)

        if owner_pid is not None and pid != owner_pid:
            emit('error', {'message': '只有房主才能开始游戏'})
            return

        if len(game.player_ids) < 4:
            emit('error', {'message': f'需要4人才能开始（当前{len(game.player_ids)}人）'})
            return

        if game.phase != 'waiting':
            emit('error', {'message': '游戏已在进行中'})
            return

        socketio.emit('message', {
            'text': '四人已到齐！游戏开始！',
            'type': 'system',
        }, room=room_id)
        game.start_game()

        # 游戏开始后向观战者广播
        spectator_pids = list(room_manager.get_spectators(room_id))
        game.broadcast_state_to_spectators(spectator_pids)

        _broadcast_room_list(socketio)

    # ── 观战 ──────────────────────────────────────────────────────
    @socketio.on('spectate')
    def on_spectate(data: dict) -> None:
        pid, _ = _resolve(request.sid)
        if pid is None:
            emit('error', {'message': '未登录'})
            return

        old_room = room_manager.get_room_id(pid)
        if old_room:
            emit('error', {'message': '你已在房间中，请先离开当前房间'})
            return

        room_id = data.get('room_id', '')
        game = room_manager.find_room_by_id(room_id)
        if not game:
            emit('error', {'message': '房间不存在'})
            return

        room_manager.add_spectator(room_id, pid)
        leave_room('lobby')
        join_room(room_id)

        uname = room_manager.get_username(pid)
        socketio.emit('message', {
            'text': f'旁观者 {uname} 开始观战',
            'type': 'info',
        }, room=room_id)

        emit('joined_room', {
            'room_id': room_id,
            'room_name': room_manager.get_room_name(room_id),
            'is_spectator': True,
        })

        # 发送观战者视角的游戏状态
        game.broadcast_state_to_spectators([pid])

        _broadcast_room_list(socketio)

    # ── 聊天 ──────────────────────────────────────────────────────
    @socketio.on('chat_message')
    def on_chat_message(data: dict) -> None:
        pid, _ = _resolve(request.sid)
        if pid is None:
            return

        text = (data.get('text', '') or '').strip()
        if not text:
            return

        # 限制消息长度
        if len(text) > 200:
            text = text[:200]

        uname = room_manager.get_username(pid)
        room_id = room_manager.get_room_id(pid)
        if not room_id:
            return

        socketio.emit('chat_message', {
            'pid': pid,
            'username': uname,
            'text': text,
        }, room=room_id)

    # ── 出牌 ───────────────────────────────────────────────────────
    @socketio.on('discard_tile')
    def on_discard(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_discard(pid, data.get('tile_code', ''))
        if not ok:
            emit('error', {'message': msg})
        else:
            # 出牌后广播给观战者
            spectator_pids = list(room_manager.get_spectators(game.room_id))
            game.broadcast_state_to_spectators(spectator_pids)

    # ── 碰/杠/胡/过 ────────────────────────────────────────────────
    @socketio.on('player_action')
    def on_action(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_action(pid, data.get('action', ''))
        if not ok:
            emit('error', {'message': msg})
        else:
            spectator_pids = list(room_manager.get_spectators(game.room_id))
            game.broadcast_state_to_spectators(spectator_pids)

    # ── 暗杠 ───────────────────────────────────────────────────────
    @socketio.on('angang')
    def on_angang(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_angang(pid, data.get('tile_code', ''))
        if not ok:
            emit('error', {'message': msg})
        else:
            spectator_pids = list(room_manager.get_spectators(game.room_id))
            game.broadcast_state_to_spectators(spectator_pids)

    # ── 补杠 ───────────────────────────────────────────────────────
    @socketio.on('bugang')
    def on_bugang(data: dict) -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_bugang(pid, data.get('tile_code', ''))
        if not ok:
            emit('error', {'message': msg})
        else:
            spectator_pids = list(room_manager.get_spectators(game.room_id))
            game.broadcast_state_to_spectators(spectator_pids)

    # ── 自摸 ───────────────────────────────────────────────────────
    @socketio.on('zimo')
    def on_zimo() -> None:
        pid, game = _resolve(request.sid)
        if game is None:
            return
        ok, msg = game.handle_zimo(pid)
        if not ok:
            emit('error', {'message': msg})
        else:
            spectator_pids = list(room_manager.get_spectators(game.room_id))
            game.broadcast_state_to_spectators(spectator_pids)

    # ── 再来一局 ───────────────────────────────────────────────────
    @socketio.on('request_new_game')
    def on_new_game() -> None:
        pid, game = _resolve(request.sid)
        if game is None or game.phase != 'ended':
            return
        game.dealer_idx = (game.dealer_idx + 1) % len(game.player_ids)
        game.start_game()
        socketio.emit('message', {'text': '新一局开始！', 'type': 'system'}, room=game.room_id)

        spectator_pids = list(room_manager.get_spectators(game.room_id))
        game.broadcast_state_to_spectators(spectator_pids)

        _broadcast_room_list(socketio)


# ── 内部工具 ────────────────────────────────────────────────────────

def _resolve(sid: str):
    """根据 sid 查找 pid 和对应的 MahjongGame，任一不存在则返回 (None, None)"""
    pid = room_manager.get_pid_by_sid(sid)
    if pid is None:
        return None, None
    game = room_manager.get_game_by_pid(pid)
    return pid, game
