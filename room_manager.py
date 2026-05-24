"""
room_manager.py — 全局房间与玩家注册表

职责：
  - 维护 players / sid_map / games 三张全局字典
  - 提供统一的查询接口（get_sid / get_username / find_player_room 等）
  - 创建/查找/清理房间（RoomManager）

设计原则：
  - 单例模式（模块级对象 room_manager）
  - 不包含任何网络 I/O；仅操作数据结构
  - MahjongGame 通过 set_player_resolver 反向注入查询接口
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game import MahjongGame
    from flask_socketio import SocketIO


class RoomManager:
    def __init__(self) -> None:
        # pid -> {username, sid, room}
        self._players: dict[int, dict] = {}
        # sid -> pid
        self._sid_map: dict[str, int] = {}
        # room_id -> MahjongGame
        self._games: dict[str, 'MahjongGame'] = {}
        self._pid_counter: int = 0
        # 房间名称：room_id -> room_name
        self._room_names: dict[str, str] = {}
        # 房主：room_id -> owner_pid
        self._room_owners: dict[str, int] = {}
        # 观战者：room_id -> set of pid
        self._spectators: dict[str, set[int]] = {}

    # ── 玩家注册 ──────────────────────────────────────────────────
    def new_player(self, sid: str, username: str | None = None) -> int:
        """注册新玩家，返回分配的 player_id"""
        self._pid_counter += 1
        pid = self._pid_counter
        uname = username or f'玩家{pid}'
        self._players[pid] = {'username': uname, 'sid': sid, 'room': None}
        self._sid_map[sid] = pid
        return pid

    def reconnect_player(self, pid: int, new_sid: str) -> bool:
        """更新玩家 sid（重连），返回是否成功"""
        if pid not in self._players:
            return False
        old_sid = self._players[pid]['sid']
        if old_sid and old_sid in self._sid_map:
            del self._sid_map[old_sid]
        self._players[pid]['sid'] = new_sid
        self._sid_map[new_sid] = pid
        return True

    def remove_sid(self, sid: str) -> int | None:
        """根据 sid 移除映射，返回对应 pid（若存在）"""
        return self._sid_map.pop(sid, None)

    def disconnect_player(self, pid: int) -> None:
        """标记玩家为离线（保留数据以支持重连）"""
        if pid in self._players:
            old_sid = self._players[pid].get('sid')
            if old_sid and old_sid in self._sid_map:
                del self._sid_map[old_sid]
            self._players[pid]['sid'] = None

    def delete_player(self, pid: int) -> None:
        """彻底删除玩家记录"""
        p = self._players.pop(pid, None)
        if p and p.get('sid'):
            self._sid_map.pop(p['sid'], None)

    # ── 玩家查询 ──────────────────────────────────────────────────
    def get_sid(self, pid: int) -> str | None:
        p = self._players.get(pid)
        return p['sid'] if p else None

    def get_username(self, pid: int) -> str:
        p = self._players.get(pid)
        return p['username'] if p else f'玩家{pid}'

    def get_pid_by_sid(self, sid: str) -> int | None:
        return self._sid_map.get(sid)

    def get_room_id(self, pid: int) -> str | None:
        p = self._players.get(pid)
        return p['room'] if p else None

    def set_room(self, pid: int, room_id: str | None) -> None:
        if pid in self._players:
            self._players[pid]['room'] = room_id

    def player_exists(self, pid: int) -> bool:
        return pid in self._players

    # ── 房间管理 ──────────────────────────────────────────────────
    def add_game(self, game: 'MahjongGame') -> None:
        self._games[game.room_id] = game

    def get_game(self, room_id: str) -> 'MahjongGame | None':
        return self._games.get(room_id)

    def get_game_by_pid(self, pid: int) -> 'MahjongGame | None':
        room_id = self.get_room_id(pid)
        return self._games.get(room_id) if room_id else None  # type: ignore

    def remove_game(self, room_id: str) -> None:
        self._games.pop(room_id, None)
        self._room_names.pop(room_id, None)
        self._room_owners.pop(room_id, None)
        self._spectators.pop(room_id, None)

    def find_open_room(self) -> 'MahjongGame | None':
        """找到等待中且未满员的房间"""
        for game in self._games.values():
            if game.phase == 'waiting' and len(game.player_ids) < 4:
                return game
        return None

    def make_room(self, socketio: 'SocketIO', room_name: str | None = None, owner_pid: int | None = None) -> 'MahjongGame':
        """创建新房间，自动注入依赖"""
        from game import MahjongGame

        rid = f'room_{random.randint(1000, 9999)}'
        while rid in self._games:
            rid = f'room_{random.randint(1000, 9999)}'

        game = MahjongGame(rid, socketio)
        game.set_player_resolver(self.get_sid, self.get_username)
        self._games[rid] = game

        # 房间名称
        self._room_names[rid] = room_name or rid
        # 房主
        if owner_pid is not None:
            self._room_owners[rid] = owner_pid
        # 观战者集合
        self._spectators[rid] = set()

        return game

    def set_room_name(self, room_id: str, name: str) -> None:
        """设置房间名称"""
        self._room_names[room_id] = name

    def get_room_name(self, room_id: str) -> str:
        """获取房间名称，若未设置则返回 room_id"""
        return self._room_names.get(room_id, room_id)

    def get_room_owner(self, room_id: str) -> int | None:
        """获取房主 pid"""
        return self._room_owners.get(room_id)

    def set_room_owner(self, room_id: str, pid: int) -> None:
        """设置房主"""
        self._room_owners[room_id] = pid

    def list_rooms(self) -> list[dict]:
        """返回所有房间信息列表，用于大厅展示"""
        result: list[dict] = []
        for rid, game in self._games.items():
            owner_pid = self._room_owners.get(rid)
            result.append({
                'room_id': rid,
                'room_name': self._room_names.get(rid, rid),
                'player_count': len(game.player_ids),
                'phase': game.phase,
                'owner_pid': owner_pid,
                'owner_name': self.get_username(owner_pid) if owner_pid else '',
                'players': [
                    {
                        'pid': p,
                        'username': self.get_username(p),
                        'seat': game.seat_name(p),
                    }
                    for p in game.player_ids
                ],
                'spectator_count': len(self._spectators.get(rid, set())),
            })
        return result

    def add_spectator(self, room_id: str, pid: int) -> None:
        """添加观战者到房间"""
        if room_id not in self._spectators:
            self._spectators[room_id] = set()
        self._spectators[room_id].add(pid)
        self._players[pid]['room'] = room_id

    def remove_spectator(self, room_id: str, pid: int) -> None:
        """从房间移除观战者"""
        if room_id in self._spectators:
            self._spectators[room_id].discard(pid)
        if pid in self._players and self._players[pid].get('room') == room_id:
            self._players[pid]['room'] = None

    def get_spectators(self, room_id: str) -> set[int]:
        """获取房间的观战者 pid 集合"""
        return self._spectators.get(room_id, set())

    def is_spectator(self, pid: int, room_id: str) -> bool:
        """判断玩家是否是某房间的观战者"""
        return pid in self._spectators.get(room_id, set())

    def find_room_by_id(self, room_id: str) -> 'MahjongGame | None':
        """根据 room_id 查找房间（get_game 的别名，语义更清晰）"""
        return self._games.get(room_id)

    # ── 调试 ──────────────────────────────────────────────────────
    def stats(self) -> dict:
        return {
            'players': len(self._players),
            'online': sum(1 for p in self._players.values() if p['sid']),
            'games': len(self._games),
        }


# 单例：整个应用只使用这一个实例
room_manager = RoomManager()
