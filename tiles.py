"""
tiles.py — 牌面定义、排序规则、Unicode 映射

职责：
  - 定义所有合法牌的编码（m1~m9, p1~p9, s1~s9, z1~z7）
  - 提供牌面排序键
  - 提供内部编码 <-> Unicode 显示字符的转换
  - 生成并洗牌（make_wall）
"""

import random

# ── Unicode 显示映射 ─────────────────────────────────────────────
UNICODE_MAP: dict[str, str] = {
    'm1': '🀇', 'm2': '🀈', 'm3': '🀉', 'm4': '🀊', 'm5': '🀋',
    'm6': '🀌', 'm7': '🀍', 'm8': '🀎', 'm9': '🀏',
    'p1': '🀙', 'p2': '🀚', 'p3': '🀛', 'p4': '🀜', 'p5': '🀝',
    'p6': '🀞', 'p7': '🀟', 'p8': '🀠', 'p9': '🀡',
    's1': '🀐', 's2': '🀑', 's3': '🀒', 's4': '🀓', 's5': '🀔',
    's6': '🀕', 's7': '🀖', 's8': '🀗', 's9': '🀘',
    'z1': '🀀', 'z2': '🀁', 'z3': '🀂', 'z4': '🀃',   # 东南西北
    'z5': '🀄', 'z6': '🀅', 'z7': '🀆',               # 中发白
}

# 反向映射：Unicode -> 内部编码（用于前端输入场景）
REVERSE_MAP: dict[str, str] = {v: k for k, v in UNICODE_MAP.items()}

# 花色排序权重：万 < 筒 < 条 < 字
_SUIT_ORDER: dict[str, int] = {'m': 0, 'p': 1, 's': 2, 'z': 3}

# 全套牌列表（用于校验）
ALL_TILES: list[str] = list(UNICODE_MAP.keys())

# 数牌花色
NUMBER_SUITS: tuple[str, ...] = ('m', 'p', 's')


def tile_sort_key(tile: str) -> tuple[int, int]:
    """
    排序键：(花色权重, 数字)
    例：m1 -> (0,1), z7 -> (3,7)
    """
    return (_SUIT_ORDER.get(tile[0], 9), int(tile[1:]))


def sort_tiles(tiles: list[str]) -> list[str]:
    """返回按万→筒→条→字排序后的新列表（不修改原列表）"""
    return sorted(tiles, key=tile_sort_key)


def to_unicode(tiles: list[str]) -> list[str]:
    """内部编码列表 -> Unicode 字符列表"""
    return [UNICODE_MAP.get(t, t) for t in tiles]


def to_display(tiles: list[str]) -> list[str]:
    """排序 + 转 Unicode（最常用的组合）"""
    return to_unicode(sort_tiles(tiles))


def tile_to_unicode(tile: str) -> str:
    """单张牌转 Unicode"""
    return UNICODE_MAP.get(tile, tile)


def make_wall() -> list[str]:
    """
    生成完整牌山（136张）并随机洗牌。
    万/筒/条各 9×4 = 108 张，字牌 7×4 = 28 张。
    """
    tiles: list[str] = []
    for suit in NUMBER_SUITS:
        for n in range(1, 10):
            tiles += [f'{suit}{n}'] * 4
    for n in range(1, 8):
        tiles += [f'z{n}'] * 4
    random.shuffle(tiles)
    return tiles


def is_valid_tile(tile: str) -> bool:
    """判断是否为合法牌编码"""
    return tile in UNICODE_MAP
