"""
game.py — 麻将游戏状态机（MahjongGame）

职责：
  - 管理单局游戏的完整状态（手牌、弃牌、副露、牌山、轮次、积分）
  - 处理出牌、碰/杠/胡、暗杠、补杠、自摸等动作
  - 向房间广播游戏状态（通过注入的 SocketIO 实例）
  - 不直接依赖 Flask request 对象

依赖：
  - tiles.py  （牌面操作）
  - logic.py  （胡牌/向听/进张算法）
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import eventlet

from tiles import (
    UNICODE_MAP,
    make_wall,
    sort_tiles,
    tile_sort_key,
    to_display,
    to_unicode,
    tile_to_unicode,
    is_valid_tile,
)
from logic import is_winning_hand, calculate_shanten, get_winning_tiles

if TYPE_CHECKING:
    from flask_socketio import SocketIO

# 座位方位名
SEAT_NAMES: list[str] = ['东', '南', '西', '北']

# 积分规则（番数）
SCORE_BASE       = 1000  # 基础底分（庄家/非庄家系数乘以此值）
SCORE_ZIMO_MULTI = 2     # 自摸时每人付双倍


class MahjongGame:
    """
    代表一局正在进行的麻将游戏。

    phase 状态机：
        waiting       → dealing（内部）
        dealing       → discard_wait
        discard_wait  → action_wait（出牌后检查响应）
        action_wait   → discard_wait（碰/杠后轮到该玩家出牌）
                     → ended（胡牌/荒牌）
        ended         → discard_wait（新一局 start_game）
    """

    def __init__(self, room_id: str, socketio: 'SocketIO') -> None:
        self.room_id = room_id
        self._sio = socketio            # 注入的 SocketIO 实例

        self.player_ids: list[int] = []
        self.hands: dict[int, list[str]] = {}
        self.discards: dict[int, list[str]] = {}
        self.melds: dict[int, list[dict]] = {}
        self.scores: dict[int, int] = {}    # 累计积分
        self.score_delta: dict[int, int] = {}  # 本局积分变动，用于结束时展示

        self.wall: list[str] = []
        self.dealer_idx: int = 0
        self.turn_idx: int = 0
        self.last_discard: tuple[int, str] | None = None

        self.phase: str = 'waiting'
        self.action_pending: dict[int, dict] = {}  # {pid: {action: bool}}
        self.action_timer = None
        self.winner: int | None = None

        # 外部注入：pid -> sid / username 的查询函数，由 room_manager 提供
        self._get_sid = lambda pid: None        # type: ignore
        self._get_username = lambda pid: f'玩家{pid}'  # type: ignore

    # ── 依赖注入 ──────────────────────────────────────────────────
    def set_player_resolver(self, get_sid, get_username) -> None:
        """注入 sid/username 查询函数，解除对全局变量的依赖"""
        self._get_sid = get_sid
        self._get_username = get_username

    # ── 玩家管理 ──────────────────────────────────────────────────
    def add_player(self, pid: int) -> bool:
        if len(self.player_ids) < 4 and pid not in self.player_ids:
            self.player_ids.append(pid)
            self.hands[pid] = []
            self.discards[pid] = []
            self.melds[pid] = []
            if pid not in self.scores:
                self.scores[pid] = 0
            return True
        return False

    def remove_player(self, pid: int) -> None:
        if pid in self.player_ids:
            self.player_ids.remove(pid)

    def seat_of(self, pid: int) -> int:
        try:
            return self.player_ids.index(pid)
        except ValueError:
            return -1

    def seat_name(self, pid: int) -> str:
        idx = self.seat_of(pid)
        return SEAT_NAMES[idx] if 0 <= idx < 4 else '?'

    @property
    def current_pid(self) -> int | None:
        if not self.player_ids:
            return None
        return self.player_ids[self.turn_idx % len(self.player_ids)]

    # ── 游戏启动 ──────────────────────────────────────────────────
    def start_game(self) -> None:
        """初始化本局，发牌，进入出牌阶段"""
        self.wall = make_wall()
        for i, pid in enumerate(self.player_ids):
            n = 14 if i == self.dealer_idx else 13
            self.hands[pid] = [self.wall.pop(0) for _ in range(n)]
            self.discards[pid] = []
            self.melds[pid] = []

        self.turn_idx = self.dealer_idx
        self.phase = 'discard_wait'
        self.last_discard = None
        self.winner = None
        self.action_pending = {}
        self.score_delta = {pid: 0 for pid in self.player_ids}

        # 庄家发14张后立即检查自摸（天胡）
        dealer_pid = self.player_ids[self.dealer_idx]
        self.broadcast_state()
        if is_winning_hand(self.hands[dealer_pid]):
            # 天胡：直接给选项
            sid = self._get_sid(dealer_pid)
            if sid:
                last = self.hands[dealer_pid][-1]
                self._emit('action_option', {
                    'options': {'hu': True},
                    'tile': tile_to_unicode(last),
                    'tile_code': last,
                    'self_draw': True,
                    'from': '天胡',
                }, room=sid)
            self.action_pending[dealer_pid] = {'hu': True}
        self._emit_turn()

    # ── 出牌 ──────────────────────────────────────────────────────
    def handle_discard(self, pid: int, tile: str) -> tuple[bool, str]:
        if self.phase == 'ended':
            return False, '游戏已结束'
        if self.phase != 'discard_wait':
            return False, '不是出牌阶段'
        if pid != self.current_pid:
            return False, '不是你的回合'
        if not is_valid_tile(tile):
            return False, '无效的牌编码'
        if tile not in self.hands[pid]:
            return False, '你没有这张牌'

        self.hands[pid].remove(tile)
        self.discards[pid].append(tile)
        self.last_discard = (pid, tile)

        self._emit(
            'tile_discarded',
            {
                'pid': pid,
                'username': self._get_username(pid),
                'seat': self.seat_name(pid),
                'tile': tile_to_unicode(tile),
                'tile_code': tile,
            },
            room=self.room_id,
        )

        self.phase = 'action_wait'
        self._check_actions(pid, tile)
        return True, 'ok'

    # ── 碰/杠/胡响应 ───────────────────────────────────────────────
    def handle_action(self, pid: int, action: str) -> tuple[bool, str]:
        if self.phase == 'ended':
            return False, '游戏已结束'
        if self.phase != 'action_wait':
            return False, '不是操作阶段'
        if pid not in self.action_pending:
            return False, '你没有可用操作'

        opts = self.action_pending.get(pid, {})

        if action == 'pass':
            del self.action_pending[pid]
            self._emit('message', {'text': f'{self._get_username(pid)} 过', 'type': 'info'}, room=self.room_id)
            if not self.action_pending:
                self._cancel_action_timer()
                self._next_turn()
            return True, 'ok'

        self._cancel_action_timer()
        discarder_pid, tile = self.last_discard  # type: ignore

        if action == 'hu' and opts.get('hu'):
            self._do_hu(pid, tile, discarder_pid, 'rong')
            return True, 'ok'

        if action == 'gang' and opts.get('gang'):
            self._do_mingang(pid, tile, discarder_pid)
            return True, 'ok'

        if action == 'peng' and opts.get('peng'):
            self._do_peng(pid, tile, discarder_pid)
            return True, 'ok'

        return False, '操作无效'

    # ── 暗杠 ──────────────────────────────────────────────────────
    def handle_angang(self, pid: int, tile: str) -> tuple[bool, str]:
        if self.phase == 'ended':
            return False, '游戏已结束'
        if self.phase != 'discard_wait' or pid != self.current_pid:
            return False, '不是你的回合'
        if not is_valid_tile(tile):
            return False, '无效的牌编码'
        if self.hands[pid].count(tile) < 4:
            return False, '没有4张相同的牌'

        for _ in range(4):
            self.hands[pid].remove(tile)
        self.melds[pid].append({'type': 'angang', 'tiles': [tile] * 4})

        self._emit('message', {'text': f'{self._get_username(pid)} 暗杠 {tile_to_unicode(tile)}', 'type': 'action'}, room=self.room_id)
        self._draw_after_gang(pid, is_angang=True)
        return True, 'ok'

    # ── 补杠 ──────────────────────────────────────────────────────
    def handle_bugang(self, pid: int, tile: str) -> tuple[bool, str]:
        if self.phase == 'ended':
            return False, '游戏已结束'
        if self.phase != 'discard_wait' or pid != self.current_pid:
            return False, '不是你的回合'
        if not is_valid_tile(tile):
            return False, '无效的牌编码'

        peng_meld = next(
            (m for m in self.melds[pid] if m['type'] == 'peng' and m['tiles'][0] == tile),
            None,
        )
        if peng_meld is None or tile not in self.hands[pid]:
            return False, '没有可补杠的牌'

        self.hands[pid].remove(tile)
        peng_meld['type'] = 'bugang'
        peng_meld['tiles'].append(tile)

        self._emit(
            'message',
            {'text': f'{self._get_username(pid)} 补杠 {tile_to_unicode(tile)}', 'type': 'action'},
            room=self.room_id,
        )

        # 补杠后其他玩家可以「抢杠胡」
        self._check_qiangganghu(pid, tile)
        return True, 'ok'

    # ── 自摸 ──────────────────────────────────────────────────────
    def handle_zimo(self, pid: int) -> tuple[bool, str]:
        if self.phase == 'ended':
            return False, '游戏已结束'
        if pid not in self.action_pending or not self.action_pending[pid].get('hu'):
            return False, '你不能自摸'
        last_tile = self.hands[pid][-1]
        self._do_hu(pid, last_tile, pid, 'zimo')
        return True, 'ok'

    # ── 内部流程：下一轮摸牌 ──────────────────────────────────────
    def _next_turn(self) -> None:
        n = len(self.player_ids)
        self.turn_idx = (self.turn_idx + 1) % n
        self.action_pending = {}

        pid = self.current_pid
        new_tile = self._draw_tile(pid)  # type: ignore
        if new_tile is None:
            return   # 荒牌已在 _draw_tile 中处理

        self.phase = 'discard_wait'

        # 检查自摸（包含岭上开花情况在 _draw_after_gang 中已处理）
        if is_winning_hand(self.hands[pid]):  # type: ignore
            sid = self._get_sid(pid)
            if sid:
                self._emit(
                    'action_option',
                    {
                        'options': {'hu': True},
                        'tile': tile_to_unicode(new_tile),
                        'tile_code': new_tile,
                        'self_draw': True,
                        'from': '自摸',
                    },
                    room=sid,
                )
            self.action_pending[pid] = {'hu': True}  # type: ignore
            self.broadcast_state()
            self._emit_turn()
            return

        sid = self._get_sid(pid)
        if sid:
            self._emit('tile_drawn', {'tile': tile_to_unicode(new_tile), 'tile_code': new_tile}, room=sid)
        self.broadcast_state()
        self._emit_turn()

    def _draw_tile(self, pid: int, from_end: bool = False) -> str | None:
        """摸牌。from_end=True 表示杠后从牌尾补张"""
        if not self.wall:
            self._declare_draw()
            return None
        tile = self.wall.pop(-1 if from_end else 0)
        self.hands[pid].append(tile)
        return tile

    def _declare_draw(self) -> None:
        """荒牌"""
        self.phase = 'ended'
        self.action_pending = {}
        self._cancel_action_timer()
        self._emit('game_over', {
            'winner': None,
            'reason': 'draw',
            'scores': {str(p): self.scores.get(p, 0) for p in self.player_ids},
            'score_delta': {str(p): 0 for p in self.player_ids},
        }, room=self.room_id)
        self.broadcast_state()

    def _draw_after_gang(self, pid: int, is_angang: bool = False) -> None:
        """杠后从牌尾补摸一张，通知玩家，并检查岭上开花"""
        new_tile = self._draw_tile(pid, from_end=True)
        self.phase = 'discard_wait'
        if new_tile is None:
            return

        sid = self._get_sid(pid)
        if sid:
            self._emit('tile_drawn', {'tile': tile_to_unicode(new_tile), 'tile_code': new_tile}, room=sid)

        # 岭上开花：杠后摸牌可以和牌
        if is_winning_hand(self.hands[pid]):
            if sid:
                self._emit(
                    'action_option',
                    {
                        'options': {'hu': True},
                        'tile': tile_to_unicode(new_tile),
                        'tile_code': new_tile,
                        'self_draw': True,
                        'from': '岭上开花',
                    },
                    room=sid,
                )
            self.action_pending[pid] = {'hu': True}

        self.broadcast_state()
        self._emit_turn()

    def _check_qiangganghu(self, gang_pid: int, tile: str) -> None:
        """
        补杠后检查其他玩家是否能「抢杠胡」。
        若无人能抢，则继续补摸（正常杠后流程）。
        """
        robbers: dict[int, dict] = {}
        for pid in self.player_ids:
            if pid == gang_pid:
                continue
            if is_winning_hand(self.hands[pid] + [tile]):
                robbers[pid] = {'hu': True}

        if not robbers:
            # 无人能抢，正常补摸
            self._draw_after_gang(gang_pid, is_angang=False)
            return

        # 发送抢杠胡提示
        self.phase = 'action_wait'
        self.last_discard = (gang_pid, tile)
        self.action_pending = robbers
        for pid, opts in robbers.items():
            sid = self._get_sid(pid)
            if sid:
                self._emit(
                    'action_option',
                    {
                        'options': opts,
                        'tile': tile_to_unicode(tile),
                        'tile_code': tile,
                        'from': f'{self._get_username(gang_pid)}（补杠）',
                        'qiangganghu': True,
                    },
                    room=sid,
                )
        self._cancel_action_timer()
        self.action_timer = eventlet.spawn_after(15, self._action_timeout)

    # ── 内部流程：检查他人响应 ────────────────────────────────────
    def _check_actions(self, discarder_pid: int, tile: str) -> None:
        """出牌后检查其他玩家是否能碰/杠/胡"""
        self.action_pending = {}

        for pid in self.player_ids:
            if pid == discarder_pid:
                continue
            opts: dict[str, bool] = {}

            # 胡（荣和）
            if is_winning_hand(self.hands[pid] + [tile]):
                opts['hu'] = True
            # 明杠
            if self.hands[pid].count(tile) == 3:
                opts['gang'] = True
            # 碰
            if self.hands[pid].count(tile) >= 2:
                opts['peng'] = True

            if opts:
                self.action_pending[pid] = opts

        if not self.action_pending:
            self._next_turn()
            return

        for pid, opts in self.action_pending.items():
            sid = self._get_sid(pid)
            if sid:
                self._emit(
                    'action_option',
                    {
                        'options': opts,
                        'tile': tile_to_unicode(tile),
                        'tile_code': tile,
                        'from': self._get_username(discarder_pid),
                    },
                    room=sid,
                )

        # 超时计时器（15 秒）
        self._cancel_action_timer()
        self.action_timer = eventlet.spawn_after(15, self._action_timeout)

    def _action_timeout(self) -> None:
        """超时自动过所有待响应"""
        if self.action_pending and self.phase == 'action_wait':
            print(f'[Room {self.room_id}] Action timeout, proceeding.')
            self.action_pending = {}
            self.action_timer = None
            self._next_turn()

    def _cancel_action_timer(self) -> None:
        if self.action_timer:
            try:
                self.action_timer.cancel()
            except Exception:
                pass
            self.action_timer = None

    # ── 具体操作 ──────────────────────────────────────────────────
    def _do_hu(self, winner_pid: int, tile: str, from_pid: int, hu_type: str) -> None:
        """处理胡牌：计分 + 广播"""
        self.winner = winner_pid
        self.phase = 'ended'
        self.action_pending = {}
        self._cancel_action_timer()

        # ── 计分 ──
        delta = self._calc_score(winner_pid, from_pid, hu_type)
        self.score_delta = delta
        for pid, d in delta.items():
            self.scores[pid] = self.scores.get(pid, 0) + d

        self._emit(
            'game_over',
            {
                'winner': winner_pid,
                'winner_name': self._get_username(winner_pid),
                'winner_seat': self.seat_name(winner_pid),
                'hu_type': hu_type,
                'winning_tile': tile_to_unicode(tile),
                'from': self._get_username(from_pid),
                'reason': 'hu',
                'scores': {str(p): self.scores.get(p, 0) for p in self.player_ids},
                'score_delta': {str(p): delta.get(p, 0) for p in self.player_ids},
            },
            room=self.room_id,
        )
        self.broadcast_state()

    def _calc_score(self, winner_pid: int, payer_pid: int, hu_type: str) -> dict[int, int]:
        """
        简单积分计算：
          - 荣和：放炮者 -1000，胡牌者 +1000
          - 自摸：其余每人 -500，胡牌者 +1500（总零和）
        """
        delta: dict[int, int] = {pid: 0 for pid in self.player_ids}
        others = [p for p in self.player_ids if p != winner_pid]

        if hu_type == 'rong':
            delta[winner_pid] = SCORE_BASE
            delta[payer_pid] = -SCORE_BASE
        else:  # zimo
            pay_each = SCORE_BASE // 2
            for pid in others:
                delta[pid] = -pay_each
            delta[winner_pid] = pay_each * len(others)

        return delta

    def _do_mingang(self, pid: int, tile: str, discarder_pid: int) -> None:
        for _ in range(3):
            self.hands[pid].remove(tile)
        self.melds[pid].append({'type': 'gang', 'tiles': [tile] * 4})

        if tile in self.discards[discarder_pid]:
            self.discards[discarder_pid].remove(tile)

        self.action_pending = {}
        self.turn_idx = self.seat_of(pid)
        self._emit('message', {'text': f'{self._get_username(pid)} 明杠 {tile_to_unicode(tile)}', 'type': 'action'}, room=self.room_id)
        self.phase = 'discard_wait'
        self._draw_after_gang(pid)

    def _do_peng(self, pid: int, tile: str, discarder_pid: int) -> None:
        for _ in range(2):
            self.hands[pid].remove(tile)
        self.melds[pid].append({'type': 'peng', 'tiles': [tile] * 3})

        if tile in self.discards[discarder_pid]:
            self.discards[discarder_pid].remove(tile)

        self.action_pending = {}
        self.turn_idx = self.seat_of(pid)
        self.phase = 'discard_wait'
        self._emit('message', {'text': f'{self._get_username(pid)} 碰 {tile_to_unicode(tile)}', 'type': 'action'}, room=self.room_id)
        self.broadcast_state()
        self._emit_turn()

    # ── 通知当前玩家轮到自己 ──────────────────────────────────────
    def _emit_turn(self) -> None:
        pid = self.current_pid
        if not pid:
            return
        sid = self._get_sid(pid)
        if not sid:
            return
        hand = self.hands.get(pid, [])
        # 进张提示（13张时才计算，避免14张时重复）
        winning_tiles = []
        if len(hand) % 3 == 1:  # 13张 => 听牌状态
            winning_tiles = [tile_to_unicode(t) for t in get_winning_tiles(hand)]

        self._emit(
            'your_turn',
            {
                'can_angang': self._check_angang(pid),
                'can_bugang': self._check_bugang(pid),
                'winning_tiles': winning_tiles,  # 进张列表（空=未听）
            },
            room=sid,
        )

    # ── 辅助查询 ──────────────────────────────────────────────────
    def _check_angang(self, pid: int) -> list[str]:
        c = Counter(self.hands[pid])
        return [tile_to_unicode(t) for t, n in c.items() if n >= 4]

    def _check_bugang(self, pid: int) -> list[str]:
        penged = {m['tiles'][0] for m in self.melds[pid] if m['type'] == 'peng'}
        seen: set[str] = set()
        result: list[str] = []
        for tile in self.hands[pid]:
            if tile in penged and tile not in seen:
                result.append(tile_to_unicode(tile))
                seen.add(tile)
        return result

    def _is_ting(self, pid: int) -> bool:
        hand = self.hands[pid]
        if len(hand) % 3 != 1:
            return False
        return calculate_shanten(hand) <= 0

    # ── 状态广播 ──────────────────────────────────────────────────
    def broadcast_state(self) -> None:
        for pid in self.player_ids:
            sid = self._get_sid(pid)
            if sid:
                self._send_state_to(pid, sid)

    def _send_state_to(self, pid: int, sid: str) -> None:
        hand = self.hands.get(pid, [])
        shanten = calculate_shanten(hand) if hand else 8
        is_ting = shanten <= 0 and len(hand) % 3 == 1

        others = [
            {
                'pid': op,
                'username': self._get_username(op),
                'seat': self.seat_name(op),
                'hand_size': len(self.hands.get(op, [])),
                'discards': to_unicode(self.discards.get(op, [])),
                'melds': self._format_melds(op, hide_angang=True),
                'connected': self._get_sid(op) is not None,
                'score': self.scores.get(op, 0),
                'score_delta': self.score_delta.get(op, 0),
            }
            for op in self.player_ids
            if op != pid
        ]

        state = {
            'phase': self.phase,
            'my_pid': pid,
            'my_seat': self.seat_name(pid),
            'my_hand': to_display(hand),
            'my_hand_codes': sort_tiles(hand),
            'my_melds': self._format_melds(pid, hide_angang=False),
            'my_discards': to_unicode(self.discards.get(pid, [])),
            'shanten': shanten,
            'is_ting': is_ting,
            'my_score': self.scores.get(pid, 0),
            'my_score_delta': self.score_delta.get(pid, 0),
            'wall_count': len(self.wall),
            'current_turn_pid': self.current_pid,
            'current_turn_seat': self.seat_name(self.current_pid) if self.current_pid else '',
            'current_turn_name': self._get_username(self.current_pid) if self.current_pid else '',
            'dealer_pid': self.player_ids[self.dealer_idx] if self.player_ids else None,
            'dealer_seat': SEAT_NAMES[self.dealer_idx],
            'last_discard_tile': (
                tile_to_unicode(self.last_discard[1]) if self.last_discard else None
            ),
            'others': others,
        }
        self._emit('game_state', state, room=sid)

    def _format_melds(self, pid: int, hide_angang: bool = False) -> list[dict]:
        result = []
        for m in self.melds.get(pid, []):
            mtype = m['type']
            tiles_display = to_unicode(m['tiles'])
            if mtype == 'angang' and hide_angang:
                tiles_display = ['🀫', '🀫', '🀫', '🀫']
            result.append({'type': mtype, 'tiles': tiles_display})
        return result

    def get_lobby_info(self) -> dict:
        return {
            'room_id': self.room_id,
            'player_count': len(self.player_ids),
            'phase': self.phase,
            'players': [
                {
                    'pid': p,
                    'username': self._get_username(p),
                    'seat': self.seat_name(p),
                    'score': self.scores.get(p, 0),
                }
                for p in self.player_ids
            ],
        }

    # ── 内部统一 emit ─────────────────────────────────────────────
    def _emit(self, event: str, data: dict, *, room: str) -> None:
        """统一封装 socketio.emit，方便日后替换传输层"""
        self._sio.emit(event, data, room=room)
