"""
scorer.py — 番型识别 + 简化计分

提供：
  - evaluate_hand(hand, melds, win_tile, hu_type, seat_wind, round_wind)
    → HandResult(fan, score, yaku_list)
  - Yaku 番种定义
  - 简化计分表

番种列表（简化版，不含吃牌相关）：
  1番：断幺九、自摸、门前清自摸、岭上开花、抢杠胡、天胡、平和、一杯口、役牌（自风/场风/三元）、暗刻×1
  2番：七对子、混全带幺、三暗刻、三色同刻、双立直（预留）
  3番：混一色、纯全带幺、二杯口
  6番：清一色
  满贯：字一色、清老头、绿一色、九莲宝灯
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from tiles import UNICODE_MAP, NUMBER_SUITS, tile_sort_key


# ── 数据结构 ────────────────────────────────────────────────────────

@dataclass
class Yaku:
    """一个番种"""
    name: str           # 中文名
    fan: int            # 番数
    yakuman: bool = False  # 是否役满

    def __str__(self):
        return f"{self.name}({self.fan}番)"


@dataclass
class HandResult:
    """胡牌结果"""
    fan: int = 0            # 总番数
    score: int = 0          # 得分（简化计分）
    yaku_list: list[Yaku] = field(default_factory=list)
    base_points: int = 0    # 基础点数

    def __str__(self):
        yakus = ', '.join(str(y) for y in self.yaku_list)
        return f"{yakus} → {self.fan}番 {self.score}分"


# ── 简化计分表 ──────────────────────────────────────────────────────

FAN_TABLE: dict[int, int] = {
    1: 1000,
    2: 2000,
    3: 4000,
    4: 8000,
    5: 8000,
    6: 12000,
    7: 12000,
    8: 16000,
    9: 16000,
    10: 16000,
    11: 24000,
    12: 24000,
    13: 32000,  # 役满
}

YAKUMAN_SCORE = 32000  # 役满基础分


def _score_for_fan(fan: int, is_yakuman: bool = False) -> int:
    """番数 → 基础得分（庄家自摸按此×2）"""
    if is_yakuman:
        return YAKUMAN_SCORE
    return FAN_TABLE.get(min(fan, 13), 8000)


# ── 手牌结构分析辅助 ──────────────────────────────────────────────

def _decompose_hand(hand: list[str], melds: list[dict]) -> dict:
    """
    分析手牌结构，返回分析结果字典。
    hand: 不含副露的手牌列表
    melds: 副露列表 [{'type': 'peng'/'gang'/'angang'/'bugang', 'tiles': [...]}]
    """
    counts = Counter(hand)
    all_tiles = hand[:]
    for m in melds:
        all_tiles.extend(m['tiles'])

    all_counts = Counter(all_tiles)

    # 花色分布
    suits_in_hand = set()
    for t in hand:
        if t[0] in NUMBER_SUITS:
            suits_in_hand.add(t[0])

    suits_in_all = set()
    for t in all_tiles:
        if t[0] in NUMBER_SUITS:
            suits_in_all.add(t[0])

    # 是否有字牌
    has_honor_in_hand = any(t[0] == 'z' for t in hand)
    has_honor_in_all = any(t[0] == 'z' for t in all_tiles)

    # 是否有幺九牌
    def is_terminal(t: str) -> bool:
        if t[0] == 'z':
            return True
        if t[0] in NUMBER_SUITS:
            n = int(t[1:])
            return n == 1 or n == 9
        return False

    has_terminal_in_hand = any(is_terminal(t) for t in hand)
    has_terminal_in_all = any(is_terminal(t) for t in all_tiles)

    # 判断是否全为数牌
    all_number = all(t[0] in NUMBER_SUITS for t in all_tiles)

    # 判断是否全为字牌
    all_honor = all(t[0] == 'z' for t in all_tiles)

    # 门清判定（没有明副露）
    is_menzen = all(m['type'] in ('angang',) for m in melds)

    # 暗刻数
    ankan_count = sum(1 for m in melds if m['type'] == 'angang')
    # 手牌中的暗刻
    hand_ankan = sum(1 for t, c in counts.items() if c >= 3)

    # 副露类型统计
    open_melds = [m for m in melds if m['type'] not in ('angang',)]
    has_open_meld = len(open_melds) > 0

    return {
        'counts': counts,
        'all_counts': all_counts,
        'all_tiles': all_tiles,
        'suits_in_hand': suits_in_hand,
        'suits_in_all': suits_in_all,
        'has_honor_in_hand': has_honor_in_hand,
        'has_honor_in_all': has_honor_in_all,
        'has_terminal_in_hand': has_terminal_in_hand,
        'has_terminal_in_all': has_terminal_in_all,
        'all_number': all_number,
        'all_honor': all_honor,
        'is_menzen': is_menzen,
        'ankan_count': ankan_count,
        'hand_ankan': hand_ankan,
        'open_melds': open_melds,
        'has_open_meld': has_open_meld,
        'is_terminal': is_terminal,
    }


def _try_decompose_to_melds(hand: list[str]) -> list[list[str]]:
    """
    将手牌分解为 雀头+面子（顺/刻）的所有可能组合。
    返回面子列表的列表，每个面子是3张牌的列表。
    """
    if len(hand) % 3 != 2:
        return []

    results = []
    counts = Counter(hand)

    def _search(cnt: Counter, melds: list[list[str]]) -> None:
        tiles = [t for t, c in cnt.items() if c > 0]
        if not tiles:
            results.append(melds[:])
            return

        tile = min(tiles, key=tile_sort_key)
        suit = tile[0]
        num = int(tile[1:])

        # 顺子
        if suit in NUMBER_SUITS and num <= 7:
            t2, t3 = f'{suit}{num+1}', f'{suit}{num+2}'
            if cnt.get(t2, 0) >= 1 and cnt.get(t3, 0) >= 1:
                cnt[tile] -= 1; cnt[t2] -= 1; cnt[t3] -= 1
                melds.append([tile, t2, t3])
                _search(cnt, melds)
                melds.pop()
                cnt[tile] += 1; cnt[t2] += 1; cnt[t3] += 1

        # 刻子
        if cnt[tile] >= 3:
            cnt[tile] -= 3
            melds.append([tile, tile, tile])
            _search(cnt, melds)
            melds.pop()
            cnt[tile] += 3

        # 无法继续 → 此路不通
        # 不需要跳过，因为外层会枚举雀头

    # 枚举雀头
    for pair_tile in list(counts.keys()):
        if counts[pair_tile] >= 2:
            counts[pair_tile] -= 2
            _search(counts, [])
            counts[pair_tile] += 2

    return results


# ── 番种检测函数 ──────────────────────────────────────────────────

def _check_tanyao(info: dict, hand: list[str], melds: list[dict]) -> Optional[Yaku]:
    """断幺九：不含幺九牌（1/9/字牌），且没有明副露含幺九"""
    # 手牌不能有幺九
    if info['has_terminal_in_hand']:
        return None
    # 明副露不能有幺九
    for m in melds:
        if m['type'] != 'angang':
            for t in m['tiles']:
                if info['is_terminal'](t):
                    return None
    return Yaku('断幺九', 1)


def _check_zimo(info: dict, hu_type: str) -> Optional[Yaku]:
    """自摸"""
    if hu_type == 'zimo':
        return Yaku('自摸', 1)
    return None


def _check_menzen_zimo(info: dict, hu_type: str) -> Optional[Yaku]:
    """门前清自摸：门清状态下自摸"""
    if hu_type == 'zimo' and info['is_menzen']:
        return Yaku('门前清自摸', 1)
    return None


def _check_lingshang(hu_type: str, from_label: str) -> Optional[Yaku]:
    """岭上开花"""
    if '岭上' in from_label:
        return Yaku('岭上开花', 1)
    return None


def _check_qianggang(hu_type: str, from_label: str) -> Optional[Yaku]:
    """抢杠胡"""
    if '抢杠' in from_label or 'qianggang' in from_label:
        return Yaku('抢杠胡', 1)
    return None


def _check_tianhu(hu_type: str, from_label: str) -> Optional[Yaku]:
    """天胡"""
    if '天胡' in from_label:
        return Yaku('天胡', 13, yakuman=True)
    return None


def _check_yakuhai(info: dict, hand: list[str], seat_wind: str, round_wind: str) -> list[Yaku]:
    """役牌：自风、场风、三元牌的刻子/杠子"""
    result = []
    wind_map = {'东': 'z1', '南': 'z2', '西': 'z3', '北': 'z4'}

    # 三元牌刻子
    for z, name in [('z5', '中'), ('z6', '发'), ('z7', '白')]:
        if info['all_counts'].get(z, 0) >= 3:
            result.append(Yaku(f'役牌·{name}', 1))

    # 自风刻子
    seat_tile = wind_map.get(seat_wind)
    if seat_tile and info['all_counts'].get(seat_tile, 0) >= 3:
        result.append(Yaku(f'役牌·自风{seat_wind}', 1))

    # 场风刻子
    round_tile = wind_map.get(round_wind)
    if round_tile and info['all_counts'].get(round_tile, 0) >= 3:
        result.append(Yaku(f'役牌·场风{round_wind}', 1))

    return result


def _check_chiitoitsu(hand: list[str]) -> Optional[Yaku]:
    """七对子"""
    if len(hand) != 14:
        return None
    counts = Counter(hand)
    if sum(1 for c in counts.values() if c >= 2) == 7:
        return Yaku('七对子', 2)
    return None


def _check_honitsu(info: dict) -> Optional[Yaku]:
    """混一色：一种数牌 + 字牌"""
    if len(info['suits_in_all']) == 1 and info['has_honor_in_all']:
        return Yaku('混一色', 3)
    return None


def _check_chinitsu(info: dict) -> Optional[Yaku]:
    """清一色：只有一种数牌，无字牌"""
    if len(info['suits_in_all']) == 1 and not info['has_honor_in_all']:
        return Yaku('清一色', 6)
    return None


def _check_toitoi(info: dict, hand: list[str], melds: list[dict]) -> Optional[Yaku]:
    """碰碰胡（对对和）：全部面子都是刻子，无顺子"""
    # 检查副露是否全是刻子类型
    for m in melds:
        if m['type'] == 'peng' or m['type'] in ('gang', 'bugang', 'angang'):
            continue
        # 如果有不明类型的副露，跳过
        return None

    # 检查手牌中是否全是刻子（不含顺子）
    decompositions = _try_decompose_to_melds(hand)
    for decomp in decompositions:
        all_triplets = all(
            len(set(meld)) == 1  # 刻子：3张相同的牌
            for meld in decomp
        )
        if all_triplets:
            return Yaku('碰碰胡', 3)
    return None


def _check_san_ankan(info: dict) -> Optional[Yaku]:
    """三暗刻"""
    total = info['ankan_count'] + info['hand_ankan']
    if total >= 3:
        return Yaku('三暗刻', 2)
    return None


def _check_suu_ankan(info: dict) -> Optional[Yaku]:
    """四暗刻（役满）"""
    total = info['ankan_count'] + info['hand_ankan']
    if total >= 4:
        return Yaku('四暗刻', 13, yakuman=True)
    return None


def _check_chanta(info: dict, hand: list[str], melds: list[dict]) -> Optional[Yaku]:
    """混全带幺：每个面子都带幺九"""
    if not info['has_honor_in_all']:
        return None
    if len(info['suits_in_all']) == 1 and not info['has_honor_in_all']:
        return None  # 清一色，不是混全带幺

    # 简化检查：所有牌都含幺九
    for t in info['all_tiles']:
        if not info['is_terminal'](t) and t[0] != 'z':
            # 不是幺九也不是字牌 → 不满足
            return None

    # 字牌本身算幺九，所以只需要检查数牌
    for t in info['all_tiles']:
        if t[0] in NUMBER_SUITS:
            n = int(t[1:])
            if n != 1 and n != 9:
                return None

    return Yaku('混全带幺', 2)


def _check_junchan(info: dict) -> Optional[Yaku]:
    """纯全带幺：每个面子都带幺九，但没有字牌"""
    if info['has_honor_in_all']:
        return None

    for t in info['all_tiles']:
        if t[0] in NUMBER_SUITS:
            n = int(t[1:])
            if n != 1 and n != 9:
                return None

    return Yaku('纯全带幺', 3)


def _check_tsuiso(info: dict) -> Optional[Yaku]:
    """字一色（役满）"""
    if info['all_honor']:
        return Yaku('字一色', 13, yakuman=True)
    return None


def _check_chinroto(info: dict) -> Optional[Yaku]:
    """清老头（役满）：只有数牌的幺九，无字"""
    if info['has_honor_in_all']:
        return None
    if not info['all_number']:
        return None
    for t in info['all_tiles']:
        if t[0] in NUMBER_SUITS:
            n = int(t[1:])
            if n != 1 and n != 9:
                return None
    return Yaku('清老头', 13, yakuman=True)


def _check_ryuiso(info: dict) -> Optional[Yaku]:
    """绿一色（役满）：只有 2/3/4/6/8 条 + 发"""
    green_tiles = {'s2', 's3', 's4', 's6', 's8', 'z6'}
    for t in info['all_tiles']:
        if t not in green_tiles:
            return None
    return Yaku('绿一色', 13, yakuman=True)


def _check_sanshoku_doko(info: dict, hand: list[str], melds: list[dict]) -> Optional[Yaku]:
    """三色同刻：三种花色有相同数字的刻子"""
    triplets = set()
    for t, c in info['all_counts'].items():
        if c >= 3 and t[0] in NUMBER_SUITS:
            triplets.add(int(t[1:]))

    # 检查是否有某个数字在三种花色都有刻子
    for num in triplets:
        suits_with = set()
        for suit in NUMBER_SUITS:
            if info['all_counts'].get(f'{suit}{num}', 0) >= 3:
                suits_with.add(suit)
        if len(suits_with) >= 3:
            return Yaku('三色同刻', 2)
    return None


# ── 主入口 ────────────────────────────────────────────────────────

def evaluate_hand(
    hand: list[str],
    melds: list[dict],
    win_tile: str,
    hu_type: str = 'rong',       # 'rong' | 'zimo'
    from_label: str = '',         # '天胡' / '岭上开花' / '抢杠' / ''
    seat_wind: str = '东',
    round_wind: str = '东',
) -> HandResult:
    """
    评估一手胡牌，返回番种、番数、得分。

    参数：
      hand:       手牌列表（不含副露取走的牌）
      melds:      副露列表
      win_tile:   胡的那张牌
      hu_type:    'rong'（荣和）或 'zimo'（自摸）
      from_label: 来源标签（天胡/岭上开花/抢杠等）
      seat_wind:  自风
      round_wind: 场风
    """
    result = HandResult()
    info = _decompose_hand(hand, melds)

    # ── 逐项检查番种 ──
    checks = []

    # 1番
    c = _check_tanyao(info, hand, melds)
    if c: checks.append(c)

    c = _check_zimo(info, hu_type)
    if c: checks.append(c)

    c = _check_menzen_zimo(info, hu_type)
    if c: checks.append(c)

    c = _check_lingshang(hu_type, from_label)
    if c: checks.append(c)

    c = _check_qianggang(hu_type, from_label)
    if c: checks.append(c)

    c = _check_tianhu(hu_type, from_label)
    if c: checks.append(c)

    # 役牌（可叠加）
    yakuhai_list = _check_yakuhai(info, hand, seat_wind, round_wind)
    checks.extend(yakuhai_list)

    # 2番
    c = _check_chiitoitsu(hand)
    if c: checks.append(c)

    c = _check_san_ankan(info)
    if c: checks.append(c)

    c = _check_sanshoku_doko(info, hand, melds)
    if c: checks.append(c)

    c = _check_chanta(info, hand, melds)
    if c: checks.append(c)

    # 3番
    c = _check_honitsu(info)
    if c: checks.append(c)

    c = _check_toitoi(info, hand, melds)
    if c: checks.append(c)

    c = _check_junchan(info)
    if c: checks.append(c)

    # 6番
    c = _check_chinitsu(info)
    if c: checks.append(c)

    # 役满
    c = _check_tsuiso(info)
    if c: checks.append(c)

    c = _check_chinroto(info)
    if c: checks.append(c)

    c = _check_ryuiso(info)
    if c: checks.append(c)

    c = _check_suu_ankan(info)
    if c: checks.append(c)

    # 如果没有番种，给一个"胡牌"底线
    if not checks:
        checks.append(Yaku('胡牌', 1))

    # 去重（同名的番种不叠加）
    seen_names = set()
    unique_checks = []
    for y in checks:
        if y.name not in seen_names:
            seen_names.add(y.name)
            unique_checks.append(y)

    # 有役满时，只保留役满番种（不叠加低番）
    has_yakuman = any(y.yakuman for y in unique_checks)
    if has_yakuman:
        unique_checks = [y for y in unique_checks if y.yakuman]

    result.yaku_list = unique_checks

    # 计算总番数
    if has_yakuman:
        total_fan = 13 * sum(1 for y in unique_checks if y.yakuman)
    else:
        total_fan = sum(y.fan for y in unique_checks)

    result.fan = total_fan
    result.base_points = _score_for_fan(total_fan, has_yakuman)
    result.score = result.base_points

    return result
