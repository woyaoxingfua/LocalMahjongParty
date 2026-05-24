"""
replay.py — 回放记录器

职责：
  - 记录单局游戏的所有操作序列
  - 导出为标准 JSON 格式
  - 保存到文件系统供后续回放
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


class ReplayRecorder:
    """
    回放记录器：捕获一局麻将的完整过程。

    数据结构：
      - version: 格式版本号
      - game_id: 唯一标识
      - start_time: 开始时间
      - players: 玩家列表
      - initial_hands: 初始手牌
      - wall: 初始牌山
      - actions: 操作序列
      - result: 对局结果
    """

    VERSION: int = 1

    def __init__(self, game_id: str, players_info: list[dict[str, Any]]) -> None:
        """
        初始化回放记录器。

        Args:
            game_id: 对局唯一标识（如 room_1234_20260523_233000）
            players_info: 玩家信息列表，每个元素含 pid, username, seat
        """
        self._data: dict[str, Any] = {
            'version': self.VERSION,
            'game_id': game_id,
            'start_time': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'players': [
                {
                    'pid': p['pid'],
                    'username': p.get('username', f"玩家{p['pid']}"),
                    'seat': p.get('seat', '?'),
                }
                for p in players_info
            ],
            'initial_hands': {},
            'wall': [],
            'actions': [],
            'result': None,
        }
        self._seq: int = 0

    def set_initial_state(self, hands: dict[int, list[str]], wall: list[str]) -> None:
        """
        记录初始手牌和牌山。

        Args:
            hands: {pid: [tile_code, ...]}
            wall: 牌山列表（剩余牌）
        """
        self._data['initial_hands'] = {str(pid): list(tiles) for pid, tiles in hands.items()}
        self._data['wall'] = list(wall)

    def record(self, action_type: str, pid: int, **kwargs: Any) -> None:
        """
        记录一个操作。

        Args:
            action_type: 操作类型（draw/discard/peng/gang/angang/bugang/draw_lingshang/hu）
            pid: 执行操作的玩家 ID
            **kwargs: 额外参数（tile, from_pid, hu_type 等）
        """
        action: dict[str, Any] = {
            'seq': self._seq,
            'type': action_type,
            'pid': pid,
        }
        # 将额外参数直接写入 action
        for key, value in kwargs.items():
            action[key] = value
        self._data['actions'].append(action)
        self._seq += 1

    def set_result(self, result_dict: dict[str, Any]) -> None:
        """
        记录对局结果。

        Args:
            result_dict: 结果字典，含 winner/hu_type/fan/score/yaku_list 等
        """
        self._data['result'] = result_dict

    def to_dict(self) -> dict[str, Any]:
        """导出为字典"""
        return dict(self._data)

    def save_to_file(self, directory: str) -> str:
        """
        保存为 JSON 文件。

        Args:
            directory: 目标目录路径

        Returns:
            保存的文件完整路径
        """
        # 确保目录存在
        os.makedirs(directory, exist_ok=True)

        game_id: str = self._data['game_id']
        filepath: str = os.path.join(directory, f'{game_id}.json')

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

        return filepath
