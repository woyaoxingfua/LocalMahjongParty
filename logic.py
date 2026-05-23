"""
logic.py — 纯算法模块（无副作用，无 I/O，无网络）

提供：
  - is_winning_hand(hand)   判断是否和牌（支持标准4副+对、七对子）
  - calculate_shanten(hand) 计算向听数（-1=和牌, 0=听牌, n=差n张）
  - get_winning_tiles(hand) 返回所有能让 13 张手牌和牌的进张列表

内部辅助：
  - _is_winning(counts)     递归回溯验证剩余牌是否能全部消耗
  - _shanten_normal(counts) 标准手型向听数
  - _shanten_chiitoitsu(counts) 七对子向听数
  - _shanten_kokushi(counts) 国士无双向听数（基础支持）
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

from tiles import tile_sort_key, NUMBER_SUITS, ALL_TILES


# ─── 和牌判断 ─────────────────────────────────────────────────────

def _is_winning(counts: dict[str, int]) -> bool:
    """
    递归回溯：判断 counts 所代表的牌是否能全部组成顺子/刻子。
    调用前已单独抽出雀头（对子），此处 counts 的总张数应是 3 的倍数。

    修正说明：
      - 先找最小牌，优先尝试顺子再尝试刻子（两条路都必须走完）
        以避免贪心刻子优先导致漏掉合法的顺子组合
    """
    tiles = [t for t, c in counts.items() if c > 0]
    if not tiles:
        return True  # 全部消耗完 => 成功

    tile = min(tiles, key=tile_sort_key)
    suit = tile[0]
    num = int(tile[1:])

    # 尝试顺子（数牌优先，保证最小牌被消耗掉）
    if suit in NUMBER_SUITS and num <= 7:
        t2 = f'{suit}{num + 1}'
        t3 = f'{suit}{num + 2}'
        if counts.get(t2, 0) >= 1 and counts.get(t3, 0) >= 1:
            counts[tile] -= 1
            counts[t2] -= 1
            counts[t3] -= 1
            ok = _is_winning(counts)
            counts[tile] += 1
            counts[t2] += 1
            counts[t3] += 1
            if ok:
                return True

    # 尝试刻子
    if counts[tile] >= 3:
        counts[tile] -= 3
        ok = _is_winning(counts)
        counts[tile] += 3
        if ok:
            return True

    # 当前最小牌既不能组顺子也不能组刻子 => 此路不通
    return False


def is_winning_hand(hand: list[str]) -> bool:
    """
    判断手牌是否和牌。
    hand：实际手牌列表（已扣除副露取走的牌），长度 mod 3 必须为 2。

    支持：
      - 标准和牌：4 副（顺/刻）+ 1 对（雀头）
      - 七对子（Chiitoitsu）：7 对不同的对子（14 张）
    """
    total = len(hand)
    if total % 3 != 2:
        return False

    counts = dict(Counter(hand))

    # 七对子（仅 14 张时检测，7张不重复的对子）
    if total == 14:
        if sum(1 for c in counts.values() if c >= 2) == 7:
            return True

    # 枚举每张牌作为雀头
    for tile in list(counts.keys()):
        if counts[tile] >= 2:
            counts[tile] -= 2
            if _is_winning(dict(counts)):   # 传副本，防止污染原字典
                counts[tile] += 2
                return True
            counts[tile] += 2

    return False


def get_winning_tiles(hand: list[str]) -> list[str]:
    """
    对一手 13 张牌，返回能让其和牌的所有「进张」列表。
    可用于听牌提示、UI 高亮。
    """
    if len(hand) != 13:
        return []
    result = []
    seen: set[str] = set()
    for tile in ALL_TILES:
        if tile in seen:
            continue
        seen.add(tile)
        test_hand = hand + [tile]
        if is_winning_hand(test_hand):
            result.append(tile)
    return result


# ─── 向听数计算（标准三合一算法）────────────────────────────────

def _shanten_normal(counts: dict[str, int], total_tiles: int) -> int:
    """
    标准手型向听数（4副+1对）。
    使用剪枝回溯，比之前的"孤张丢弃"版本快且准确。
    """
    # 估算: (需要凑满的副数)*2 - 已成副数*2 - 搭子数 - 有无雀头
    # 最多需要 (total/3) 副 + 1 对
    need_melds = (total_tiles - 2) // 3  # 应组成的副数
    best = [need_melds * 2]              # 最坏情况

    def search(cnts: dict, melds: int, partial: int, pairs: int, has_pair: bool) -> None:
        # 当前估算
        val = need_melds - melds - partial - (1 if has_pair else 0)
        if val < best[0]:
            best[0] = val
        if best[0] == -1:
            return
        if melds + partial >= need_melds + (0 if has_pair else 0):
            return

        tiles = sorted([t for t, c in cnts.items() if c > 0], key=tile_sort_key)
        if not tiles:
            return

        t = tiles[0]
        s = t[0]
        n = int(t[1:])

        # 尝试顺子
        if s in NUMBER_SUITS and n <= 7:
            t2, t3 = f'{s}{n+1}', f'{s}{n+2}'
            if cnts.get(t2, 0) >= 1 and cnts.get(t3, 0) >= 1:
                nc = dict(cnts); nc[t] -= 1; nc[t2] -= 1; nc[t3] -= 1
                search(nc, melds + 1, partial, pairs, has_pair)

        # 尝试刻子
        if cnts[t] >= 3:
            nc = dict(cnts); nc[t] -= 3
            search(nc, melds + 1, partial, pairs, has_pair)

        # 尝试雀头（只选一次）
        if not has_pair and cnts[t] >= 2:
            nc = dict(cnts); nc[t] -= 2
            search(nc, melds, partial, pairs + 1, True)

        # 尝试顺搭（两面/嵌张）
        if s in NUMBER_SUITS:
            for d in (1, 2):
                t2 = f'{s}{n+d}'
                if cnts.get(t2, 0) >= 1:
                    nc = dict(cnts); nc[t] -= 1; nc[t2] -= 1
                    search(nc, melds, partial + 1, pairs, has_pair)
                    break  # 每张牌只取最近的搭子

        # 尝试对搭（单对，留作雀头候选）
        if not has_pair and cnts[t] >= 2:
            pass  # 已在上面的"雀头"中处理

        # 剪枝：最小牌无法用 => 跳过（不丢弃，改为跳到下一张）
        nc = dict(cnts); nc[t] -= 1
        if nc[t] == 0:
            del nc[t]
        search(nc, melds, partial, pairs, has_pair)

    search(dict(counts), 0, 0, 0, False)
    return best[0]


def _shanten_chiitoitsu(counts: dict[str, int]) -> int:
    """七对子向听数：6 - 已有对数"""
    pairs = sum(1 for c in counts.values() if c >= 2)
    return 6 - pairs


def _shanten_kokushi(counts: dict[str, int]) -> int:
    """
    国士无双向听数：
    需要 1m,9m,1p,9p,1s,9s,z1~z7 各1张 + 其中任意1张重复作雀头
    """
    yaochuhai = ['m1','m9','p1','p9','s1','s9','z1','z2','z3','z4','z5','z6','z7']
    unique = sum(1 for t in yaochuhai if counts.get(t, 0) >= 1)
    has_pair = any(counts.get(t, 0) >= 2 for t in yaochuhai)
    return 13 - unique - (1 if has_pair else 0)


def calculate_shanten(hand: list[str]) -> int:
    """
    计算向听数。
      -1 = 已和牌
       0 = 听牌（差1张）
       n = 差 n 张

    同时考虑标准手型（4副+对）、七对子、国士无双。
    """
    if not hand:
        return 8

    counts = dict(Counter(hand))
    total = len(hand)

    std = _shanten_normal(counts, total)
    chii = _shanten_chiitoitsu(counts)
    koku = _shanten_kokushi(counts)

    return min(std, chii, koku)
