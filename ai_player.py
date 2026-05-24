"""
ai_player.py — AI 托管策略模块

职责：
  - 为断线玩家提供自动出牌/碰杠胡决策
  - 简单策略：优先出孤张、安全牌；总是胡/碰/杠

策略说明：
  - 出牌：优先出孤张（手牌中只有1张且非进张的牌），其次出非进张的安全牌
  - 碰/杠/胡：总是胡、总是碰/杠（简单策略）
  - 过：无操作时自动过
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from tiles import NUMBER_SUITS, tile_sort_key, sort_tiles
from logic import is_winning_hand, calculate_shanten, get_winning_tiles


def ai_choose_discard(
    hand: list[str],
    melds: list[dict] | None = None,
    discards: list[str] | None = None,
) -> str:
    """
    AI 选择出牌策略。

    优先级（从高到低）：
    1. 孤张字牌（手里只有1张的字牌）
    2. 孤张数牌（手里只有1张的数牌，且不是进张）
    3. 非进张的数牌（向听数不减少的出牌）
    4. 向听数增加最少的出牌
    5. 手牌最后一张（兜底）

    参数：
      hand: 当前手牌列表
      melds: 副露列表（暂未使用，预留）
      discards: 全场弃牌列表（暂未使用，预留）

    返回：
      选出的牌编码（str）
    """
    if not hand:
        return ''

    if len(hand) == 1:
        return hand[0]

    counts = Counter(hand)
    current_shanten = calculate_shanten(hand)

    # ── 1. 找孤张字牌 ──
    isolated_honors: list[str] = []
    for tile, cnt in counts.items():
        if tile[0] == 'z' and cnt == 1:
            isolated_honors.append(tile)

    if isolated_honors:
        # 优先出役牌以外的字牌，如果有多个就随便选
        non_yakuhai = [t for t in isolated_honors if t not in ('z5', 'z6', 'z7')]
        if non_yakuhai:
            return _pick_safe_tile(non_yakuhai, discards)
        return _pick_safe_tile(isolated_honors, discards)

    # ── 2. 找孤张数牌（只有1张，且周围无搭子）──
    isolated_numbers: list[str] = []
    for tile, cnt in counts.items():
        if tile[0] in NUMBER_SUITS and cnt == 1:
            suit = tile[0]
            num = int(tile[1:])
            # 检查是否有邻牌搭子
            has_neighbor = False
            for d in (-2, -1, 1, 2):
                neighbor = f'{suit}{num + d}'
                if neighbor in counts and counts[neighbor] > 0:
                    has_neighbor = True
                    break
            if not has_neighbor:
                isolated_numbers.append(tile)

    if isolated_numbers:
        # 优先出边张（1或9）
        edge_tiles = [t for t in isolated_numbers if int(t[1:]) in (1, 9)]
        if edge_tiles:
            return _pick_safe_tile(edge_tiles, discards)
        return _pick_safe_tile(isolated_numbers, discards)

    # ── 3. 向听数不增加的出牌（打掉后向听数不变）──
    safe_discards: list[str] = []
    shanten_increase: list[tuple[int, str]] = []

    for tile in sorted(set(hand), key=tile_sort_key):
        # 跳过对子/刻子核心牌（避免打关键牌）
        if counts[tile] >= 2:
            # 对子以上的牌，打掉一张后检查向听
            test_hand = hand[:]
            test_hand.remove(tile)
            new_shanten = calculate_shanten(test_hand)
            diff = new_shanten - current_shanten
            if diff <= 0:
                safe_discards.append((tile, diff))
            else:
                shanten_increase.append((diff, tile))
        else:
            # 单张牌
            test_hand = hand[:]
            test_hand.remove(tile)
            new_shanten = calculate_shanten(test_hand)
            diff = new_shanten - current_shanten
            if diff <= 0:
                safe_discards.append((tile, diff))
            else:
                shanten_increase.append((diff, tile))

    # 优先出向听数不变的单张
    no_increase = [t for t, d in safe_discards if d == 0 and counts[t] == 1]
    if no_increase:
        return _pick_safe_tile(no_increase, discards)

    # 其次出向听数不变的其他牌
    no_increase_all = [t for t, d in safe_discards if d == 0]
    if no_increase_all:
        return _pick_safe_tile(no_increase_all, discards)

    # 出向听数增加最少的牌
    if shanten_increase:
        shanten_increase.sort(key=lambda x: x[0])
        min_diff = shanten_increase[0][0]
        best = [t for d, t in shanten_increase if d == min_diff]
        # 优先出单张
        singles = [t for t in best if counts[t] == 1]
        if singles:
            return _pick_safe_tile(singles, discards)
        return _pick_safe_tile(best, discards)

    # ── 兜底：出最后一张 ──
    return hand[-1]


def ai_should_action(options: dict) -> str:
    """
    AI 决定是否执行碰/杠/胡操作。

    策略：
      - 胡：总是胡
      - 杠：总是杠
      - 碰：总是碰
      - 无操作：过

    参数：
      options: 可用操作字典，如 {'hu': True, 'gang': True, 'peng': True}

    返回：
      选择的操作名称（'hu', 'gang', 'peng', 'pass'）
    """
    if options.get('hu'):
        return 'hu'
    if options.get('gang'):
        return 'gang'
    if options.get('peng'):
        return 'peng'
    return 'pass'


def ai_should_zimo(can_hu: bool) -> bool:
    """
    AI 决定是否自摸。

    参数：
      can_hu: 是否可以自摸胡牌

    返回：
      True 表示自摸，False 表示放弃
    """
    return can_hu


def ai_choose_angang(hand: list[str]) -> Optional[str]:
    """
    AI 选择暗杠的牌。

    参数：
      hand: 当前手牌

    返回：
      可暗杠的牌编码，或 None
    """
    counts = Counter(hand)
    angang_candidates = [tile for tile, cnt in counts.items() if cnt >= 4]
    if angang_candidates:
        return angang_candidates[0]
    return None


def ai_choose_bugang(hand: list[str], melds: list[dict]) -> Optional[str]:
    """
    AI 选择补杠的牌。

    参数：
      hand: 当前手牌
      melds: 副露列表

    返回：
      可补杠的牌编码，或 None
    """
    penged = {m['tiles'][0] for m in melds if m['type'] == 'peng'}
    for tile in hand:
        if tile in penged:
            return tile
    return None


def _pick_safe_tile(
    candidates: list[str],
    discards: list[str] | None = None,
) -> str:
    """
    从候选牌中选择最安全的一张出牌。

    安全性判断：已经在弃牌池中出现越多的牌，越安全（别人不要的概率高）。

    参数：
      candidates: 候选牌列表
      discards: 全场弃牌列表

    返回：
      选出的牌编码
    """
    if not candidates:
        return ''

    if not discards:
        # 没有弃牌信息时，选排序最大的（通常是边角牌）
        return sorted(candidates, key=tile_sort_key)[-1]

    discard_counts = Counter(discards)

    # 按弃牌池中出现的次数排序（出现越多越安全）
    def safety_key(tile: str) -> int:
        return discard_counts.get(tile, 0)

    candidates.sort(key=safety_key, reverse=True)
    return candidates[0]
