"""The visual vocabulary as the script model reads it.

One source of truth for what the stage can do, shared by the script prompt
and by anyone reading a script. Kept short: the model has to hold this in
mind while writing 160 lines of dialogue, and every extra rule here is a rule
it will forget somewhere else.
"""

from __future__ import annotations

from . import OPS, SLOTS
from . import elements as E
from . import illustrations as ILL
from . import style as S

VISUAL_MANUAL = f"""\
## 図の書き方（visual）

画面は「1枚の板」です。1行のセリフにつき、板に対する操作を1〜3個書きます。
**話に出てくる物は、その物の絵を置く**（物差しの話なら ruler、海なら wave_icon、家なら house）。
言葉を箱に入れる concept は、絵にできない抽象語のときだけ。ラベルは絵に添える。
板は消すまで残るので、図は行ごとに1手ずつ育てます（置く→矢印→ラベル→強調）。
新しい話に移るときだけ clear します。章の最初の行は必ず clear から始めます。

書式: 1操作=1行 `op key=value key=value`。リストは `|` 区切り、要素内は `:` 区切り、
表の行は `;` 区切り、文中の改行は `/`。位置はピクセルではなく**スロット名**で指定します。

スロット: {", ".join(SLOTS)}
（sky=左上の太陽、floor=画面下の地面、top=上段フル幅、wide=年表向き、
  left/right=左右対比、center=主図、*-third=3分割）
色: {", ".join(S.PALETTE)}（既定 white。強調は yellow、対比は cyan と pink/magenta）

### 操作
- `clear [keep_heading=true]` 板を消す
- `background name=parchment|space|room` 背景（章の最初に）
- `heading text=… [colour=yellow align=left]` 上部見出し（人名・節名）
- `title text=… [dim=true]` 中央に大きな題。dim=true で図を暗くして上に出す
- `label text=… at=<スロット|要素名|要素名.部位> [side=above|below|left|right|on] [colour=] [size=] [pointer=true] [name=]`
  pointer=true で対象へ小さな矢印を出す。text は12文字以内。強調したい語は size=large、結論は title。
- `arrow from=<参照> to=<参照|up|down|left|right|up-right|…> [via=<参照>] [length=] [colour=] [heads=2] [dashed=true] [bulge=]`
  参照はスロット名・要素名・`要素名.部位`。via で折れ曲がる（太陽→氷→反射）
- `add element=<要素> near=<要素名> name=…` 既存の要素の隣・上に追加（ice_block は地面の上、moon は軌道）
- `<要素名> slot=<スロット> name=… [要素固有の引数]` 要素を置く（place の省略形）
- `dim` ここまでの図を暗くする。以後に描くものは明るい
- `highlight target=<参照>` 黄色の楕円で囲む　`strike target=<要素名>` 打ち消し線
- `list_add text=…` 右上に番号付きで1行追加（使うのは列挙が本当に要るときだけ）
- `expression explainer=<表情> listener=<表情>` 表情（normal happy surprised thinking sad）
- `hold` 板を変えない（連続は2行まで）
- `zoom from=<参照> element=<要素> slot=right` 虫眼鏡で拡大　`compare elements=a|b labels=A|B` 左右並べ

### 要素（`<要素名> slot=… name=…` で置く）
- 天体: sun, earth_globe [ice=true], earth_arc [colour=green|orange], moon, galaxy, black_blob, star_dots
- 物: ice_block, cloud, atom [electrons=2], charge [sign=+|-], telescope, person [label=…], balance [left=… right=… tilt=-1..1]
- 概念: concept text=…（言葉だけを箱に）, wave [cycles=4 stretch=1.6 colour=]（光・音・赤方偏移。stretch で波長が伸びる）,
  ladder rungs=近く|中|遠く（距離はしご・段階）, scatter points=x:y[:ラベル]|… [x_label= y_label= line=true]（点と直線のグラフ。部位=p1,p2…とラベル）
- 構造図:
  - pie slices=名前:値:色[:注記]|…  [centre_label=…]   部位=各名前
  - box_row items=語|語|…                              部位=各語
  - timeline start=… end=… bands=from:to:色[:name]|…   部位=各name, band, axis
  - number_line low=小 high=大 [vertical=true] markers=位置:ラベル[:色[:side]]|… [span=a:b span_label=…]   部位=各ラベル
  - table columns=A|B rows=セル|セル;セル|セル [highlight=セル文]   ➡ ◎ × は図形になる。部位=row1…, セルの文, r1c2
  - chain nodes=文:x:y[:色[:size]]|… edges=0-1|2-3        x,y は 0〜1。部位=各文
  - columns items=題:色:本文/改行|…                        部位=各題, 題.text
  - panel x=時間 y=高さ（空間） [warp=0.6]                  白い格子。部位=cell_c_r, origin
- **絵**（話に出てくる物は、言葉の箱ではなく絵を置く）: `<名前> slot=… name=… [size=small|large]`
{ILL.manual_lines()}
  例: 「物差し」の話なら `ruler slot=left name=r1`、「家」なら `house slot=right`。同じ絵を2つ置いて比べてもよい。

### 例（1行のセリフ＝1〜3操作）
1. 「太陽の光が氷に当たると…」 → `sun slot=sky name=sun` / `earth_arc slot=floor name=earth` / `add element=ice_block near=earth name=ice1`
2. 「そのまま跳ね返されてしまうのだ」 → `arrow from=sun via=ice1.top to=up-right colour=yellow`
3. 「宇宙の68%はダークエネルギー」 → `pie slot=center name=pie centre_label=宇宙全体 slices=普通の物質:5:white|ダークマター:27:cyan|ダークエネルギー:68:magenta:宇宙を膨張させる/謎のエネルギー`
4. 「この2つがミクロな世界の力なのだ」 → `label text=ミクロな世界の力 at=bottom-right name=micro` / `arrow from=forces.強い核力 to=micro` / `arrow from=forces.弱い核力 to=micro`
5. 「45万年の間に氷期と間氷期を繰り返してきた」 → `timeline slot=wide name=tl start=45万年前 end=現在 bands=0:1:cyan:cold|0.14:0.18:pink:warm1|0.33:0.37:pink:warm2` / `label text=氷期 at=tl.cold side=above colour=cyan pointer=true`
6. 「これを正のフィードバックと呼ぶのだ」 → `title text=正のフィードバック dim=true`
"""


def op_names() -> tuple[str, ...]:
    return OPS


def element_names() -> list[str]:
    return sorted(E.REGISTRY)
