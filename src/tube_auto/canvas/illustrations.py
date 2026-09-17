"""The everyday illustrations the script may place, by name.

Fetched from Noto Emoji (Apache-2.0) by tools/fetch_illustrations.py into
assets/illustrations/<name>.png. The script model only draws what it is
told exists; this list is what the manual shows it, with the Japanese
word it will be thinking in.
"""

from __future__ import annotations

# name -> (Noto codepoint(s), 日本語)
ILLUSTRATIONS: dict[str, tuple[str, str]] = {
    # measuring, tools
    "ruler": ("1f4cf", "物差し"), "tape_measure": ("1f4d0", "三角定規"), "clock": ("23f0", "目覚まし時計"),
    "stopwatch": ("23f1", "ストップウォッチ"), "thermometer": ("1f321", "温度計"), "scale": ("2696", "天秤"),
    "magnet": ("1f9f2", "磁石"), "bulb": ("1f4a1", "電球"), "battery": ("1f50b", "電池"), "flashlight": ("1f526", "懐中電灯"),
    "microscope": ("1f52c", "顕微鏡"), "telescope_icon": ("1f52d", "望遠鏡"), "satellite_dish": ("1f4e1", "パラボラアンテナ"),
    "satellite": ("1f6f0", "人工衛星"), "rocket": ("1f680", "ロケット"), "test_tube": ("1f9ea", "試験管"),
    "dna": ("1f9ec", "DNA"), "gear": ("2699", "歯車"), "camera": ("1f4f7", "カメラ"), "book": ("1f4d6", "本"),
    "notebook": ("1f4d3", "ノート"), "pencil": ("270f", "鉛筆"), "calculator": ("1f9ee", "電卓"), "compass": ("1f9ed", "方位磁石"),
    # everyday
    "house": ("1f3e0", "家"), "school": ("1f3eb", "学校"), "car": ("1f697", "車"), "train": ("1f686", "電車"), "bicycle": ("1f6b2", "自転車"),
    "airplane": ("2708", "飛行機"), "ship": ("1f6a2", "船"), "ball": ("26bd", "サッカーボール"), "apple": ("1f34e", "りんご"),
    "glass": ("1f95b", "コップ"), "cup": ("2615", "カップ"), "ice_cube": ("1f9ca", "氷"), "fire": ("1f525", "炎"),
    "candle": ("1f56f", "ろうそく"), "coin": ("1fa99", "コイン"), "money": ("1f4b0", "お金"), "umbrella": ("2602", "傘"),
    "mirror": ("1fa9e", "鏡"), "window": ("1fa9f", "窓"), "door": ("1f6aa", "ドア"), "bed": ("1f6cf", "ベッド"),
    "phone": ("1f4f1", "スマホ"), "tv": ("1f4fa", "テレビ"), "radio": ("1f4fb", "ラジオ"), "eye": ("1f441", "目"),
    "ear": ("1f442", "耳"), "hand": ("270b", "手"), "brain": ("1f9e0", "脳"), "footprints": ("1f463", "足あと"),
    "scientist": ("1f9d1_200d_1f52c", "科学者"), "astronaut": ("1f9d1_200d_1f680", "宇宙飛行士"), "teacher": ("1f9d1_200d_1f3eb", "先生"),
    "question": ("2753", "はてな"), "warning": ("26a0", "注意"), "check": ("2705", "チェック"), "cross": ("274c", "バツ"),
    # nature and sky
    "sun_icon": ("2600", "太陽"), "moon_icon": ("1f319", "三日月"), "full_moon": ("1f315", "満月"), "star_icon": ("2b50", "星"),
    "sparkles": ("2728", "きらきら"), "comet": ("2604", "彗星"), "ringed_planet": ("1fa90", "土星"), "earth_icon": ("1f30d", "地球"),
    "milky_way": ("1f30c", "天の川"), "night": ("1f303", "夜景"), "sunrise": ("1f305", "日の出"), "rainbow": ("1f308", "虹"),
    "cloud_icon": ("2601", "雲"), "rain": ("1f327", "雨"), "snow": ("1f328", "雪"), "lightning": ("26a1", "稲妻"),
    "tornado": ("1f32a", "竜巻"), "wind": ("1f32c", "風"), "wave_icon": ("1f30a", "波"), "volcano": ("1f30b", "火山"),
    "mountain": ("26f0", "山"), "snow_mountain": ("1f3d4", "雪山"), "desert": ("1f3dc", "砂漠"), "island": ("1f3dd", "島"),
    "tree": ("1f333", "木"), "seedling": ("1f331", "芽"), "flower": ("1f33b", "ひまわり"), "leaf": ("1f343", "葉"),
    "dog": ("1f436", "犬"), "cat": ("1f431", "猫"), "bird": ("1f426", "鳥"), "fish": ("1f41f", "魚"), "whale": ("1f40b", "クジラ"),
    "dinosaur": ("1f996", "恐竜"), "butterfly": ("1f98b", "蝶"), "bacteria": ("1f9a0", "微生物"), "snowflake": ("2744", "雪の結晶"),
    "droplet": ("1f4a7", "水滴"), "hourglass": ("23f3", "砂時計"), "globe": ("1f310", "地球儀"), "map": ("1f5fa", "地図"),
}


def japanese(name: str) -> str:
    return ILLUSTRATIONS.get(name, ("", name))[1]


def manual_lines() -> str:
    """The catalogue as the prompt shows it: 日本語=名前, grouped loosely."""
    items = [f"{jp}={name}" for name, (_, jp) in ILLUSTRATIONS.items()]
    lines = []
    for i in range(0, len(items), 12):
        lines.append("  " + " ".join(items[i:i + 12]))
    return "\n".join(lines)
