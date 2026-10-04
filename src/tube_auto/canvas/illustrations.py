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
    # sound and music (a black hole's ringdown is a bell)
    "bell": ("1f514", "鐘"), "drum": ("1f941", "太鼓"), "guitar": ("1f3b8", "ギター"), "violin": ("1f3bb", "バイオリン"),
    "piano": ("1f3b9", "ピアノ"), "trumpet": ("1f3ba", "トランペット"), "speaker": ("1f50a", "スピーカー"), "note": ("1f3b5", "音符"),
    "microphone": ("1f3a4", "マイク"), "headphones": ("1f3a7", "ヘッドホン"), "tuning_fork": ("1f3bc", "楽譜"),
    "wave_sound": ("1f4e2", "拡声器"), "ripple": ("1f30a", "波紋"),
    # more everyday
    "balloon": ("1f388", "風船"), "spring": ("1f9f7", "安全ピン"), "rope": ("1faa2", "ロープ"), "rock": ("1faa8", "岩"),
    "bread": ("1f35e", "パン"), "egg": ("1f95a", "卵"), "milk": ("1f95b", "牛乳"), "salt": ("1f9c2", "塩"),
    "bath": ("1f6c1", "お風呂"), "sponge": ("1f9fd", "スポンジ"), "elevator": ("1f6d7", "エレベーター"), "stairs": ("1f6d7", "階段"),
    "trophy": ("1f3c6", "トロフィー"), "target": ("1f3af", "的"), "dice": ("1f3b2", "サイコロ"), "puzzle": ("1f9e9", "パズル"),
    "key": ("1f511", "鍵"), "lock": ("1f512", "錠"), "package": ("1f4e6", "箱"), "scissors": ("2702", "はさみ"),
    "hammer": ("1f528", "ハンマー"), "wrench": ("1f527", "スパナ"), "link": ("1f517", "鎖"), "chart_up": ("1f4c8", "上がるグラフ"),
    "chart_down": ("1f4c9", "下がるグラフ"), "shield": ("1f6e1", "盾"), "crystal_ball": ("1f52e", "水晶玉"), "dizzy": ("1f4ab", "目が回る"),
    # places
    "temple": ("1f6d5", "寺"), "shrine": ("26e9", "神社"), "tokyo_tower": ("1f5fc", "東京タワー"), "mount_fuji": ("1f5fb", "富士山"),
    "castle": ("1f3ef", "城"), "city": ("1f3d9", "街"), "factory": ("1f3ed", "工場"), "hospital": ("1f3e5", "病院"),
    "stadium": ("1f3df", "スタジアム"), "tent": ("26fa", "テント"), "bridge": ("1f309", "橋"), "beach": ("1f3d6", "海辺"),
    "park": ("1f3de", "公園"), "road": ("1f6e3", "道路"), "lab": ("1f52c", "研究室"), "office": ("1f3e2", "ビル"),
    # people
    "child": ("1f9d2", "子ども"), "student": ("1f9d1_200d_1f393", "学生"), "old_man": ("1f474", "おじいさん"), "old_woman": ("1f475", "おばあさん"),
    "doctor": ("1f9d1_200d_2695", "医者"), "cook": ("1f9d1_200d_1f373", "料理人"), "farmer": ("1f9d1_200d_1f33e", "農家"),
    "detective": ("1f575", "探偵"), "runner": ("1f3c3", "走る人"), "thinker": ("1f914", "考える顔"), "surprised_face": ("1f632", "驚く顔"),
    "sleeping": ("1f634", "眠る顔"), "family": ("1f46a", "家族"), "crowd": ("1f465", "人々"), "baby": ("1f476", "赤ちゃん"),
    # vehicles and animals
    "bus": ("1f68c", "バス"), "truck": ("1f69a", "トラック"), "helicopter": ("1f681", "ヘリコプター"), "sailboat": ("26f5", "ヨット"),
    "elephant": ("1f418", "象"), "mouse": ("1f42d", "ねずみ"), "ant": ("1f41c", "アリ"), "turtle": ("1f422", "カメ"),
    "rabbit": ("1f430", "うさぎ"), "horse": ("1f434", "馬"), "cow": ("1f42e", "牛"), "penguin": ("1f427", "ペンギン"),
    "frog": ("1f438", "カエル"), "bee": ("1f41d", "ハチ"), "octopus": ("1f419", "タコ"), "shark": ("1f988", "サメ"),
    # more objects that come up
    "sword": ("1f5e1", "剣"), "bow": ("1f3f9", "弓矢"), "boomerang": ("1fa83", "ブーメラン"), "kite": ("1fa81", "凧"),
    "yo_yo": ("1fa80", "ヨーヨー"), "top": ("1f3b0", "スロット"), "magnifier": ("1f50d", "虫めがね"), "glasses": ("1f453", "めがね"),
    "lantern": ("1f3ee", "提灯"), "fan": ("1faad", "うちわ"), "rice": ("1f35a", "ごはん"), "sushi": ("1f363", "寿司"),
    "onigiri": ("1f359", "おにぎり"), "tea": ("1f375", "お茶"), "newspaper": ("1f4f0", "新聞"), "letter": ("2709", "手紙"),
    "trophy2": ("1f947", "金メダル"), "medal": ("1f3c5", "メダル"), "flag": ("1f6a9", "旗"), "bomb": ("1f4a3", "爆弾"),
    "explosion": ("1f4a5", "爆発"), "smoke": ("1f4a8", "煙"), "hole": ("1f573", "穴"), "spiral": ("1f300", "渦"),
    "thermometer_face": ("1f912", "熱がある顔"), "cold_face": ("1f976", "寒い顔"), "hot_face": ("1f975", "暑い顔"),
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
