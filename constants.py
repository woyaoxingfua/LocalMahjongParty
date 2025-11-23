# Mahjong Tile Representation
# Unicode characters for Mahjong tiles (simplified for now, will expand as needed)
# Manzu (Characters)
MANZU = ["🀇", "🀈", "🀉", "🀊", "🀋", "🀌", "🀍", "🀎", "🀏"]
# Pinzu (Circles)
PINZU = ["🀙", "🀚", "🀛", "🀜", "🀝", "🀞", "🀟", "🀠", "🀡"]
# Souzu (Bamboos)
SOUZU = ["🀐", "🀑", "🀒", "🀓", "🀔", "🀕", "🀖", "🀗", "🀘"]
# Zihai (Winds and Dragons)
ZIHAI = ["🀀", "🀁", "🀂", "🀃", "🀄", "🀅", "🀆"] # East, South, West, North, Haku, Hatsu, Chun

ALL_TILES = (MANZU * 4) + (PINZU * 4) + (SOUZU * 4) + (ZIHAI * 4) # 4 sets of each tile, total 136

# Tile conversion helper
TILE_MAP = {
    "🀇": "m1", "🀈": "m2", "🀉": "m3", "🀊": "m4", "🀋": "m5", "🀌": "m6", "🀍": "m7", "🀎": "m8", "🀏": "m9",
    "🀙": "p1", "🀚": "p2", "🀛": "p3", "🀜": "p4", "🀝": "p5", "🀞": "p6", "🀟": "p7", "🀠": "p8", "🀡": "p9",
    "🀐": "s1", "🀑": "s2", "🀒": "s3", "🀓": "s4", "🀔": "s5", "🀕": "s6", "🀖": "s7", "🀗": "s8", "🀘": "s9",
    "🀀": "e", "🀁": "s", "🀂": "w", "🀃": "n", # Winds
    "🀄": "h", "🀅": "f", "🀆": "c"  # Dragons (Haku, Hatsu, Chun)
}
REVERSE_TILE_MAP = {v: k for k, v in TILE_MAP.items()}

# Configuration for special hands
DEFAULT_SPECIAL_HANDS_CONFIG = {
    "pinhu": True,
    "iipeikou": True,
    "chantaiyao": True,
    "junchantaiyao": True,
    "honchantaiyao": True,
    "ittsuu": True,
    "ryanpeikou": True,
    "sanshokudoujun": True,
    "sanshokudoukou": True,
    "chanta": True,
    "honroutou": True,
    "shousangen": True,
    "honitsu": True,
    "chinitu": True,
    "tenhou": True,
    "chihihou": True,
    "rinshankaihou": True,
    "chankan": True,
    "haiteiraoyue": True,
    "houteiraoyui": True,
    "daisangen": True,
    "suuankou": True,
    "suuankoutanki": True,
    "tsuuiisou": True,
    "ryuuiisou": True,
    "chinroutou": True,
    "chuurenpoutou": True,
    "kunroutou": True,
    "daisuushi": True,
    "shosuushi": True,
    "suukantsu": True
}