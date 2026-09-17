# assets/ — 手で集める素材

パイプラインが自動では取りに行かない素材を置く場所。`library.yaml` に1ファイルずつ出典とライセンスを記録する（publish 段階が概要欄のクレジットに使う）。

## sprites/ — 立ち絵

```
sprites/zundamon/normal_closed.png   口を閉じた通常
sprites/zundamon/normal_open.png     口を開けた通常（口パク用）
sprites/zundamon/happy_closed.png    happy / surprised / thinking / sad も同様
sprites/zundamon/happy_open.png
sprites/metan/…                      四国めたんも同じ構成
```

- 透過PNG。縦横どちらかが 320px 以上あればよい（描画時に縮小される）。
- `normal_closed.png` だけあれば動く。無い表情は normal に、無い口開きは閉じにフォールバックする。

### どこから取るか

坂本アヒル氏の PSDTool 対応立ち絵（ニコニコ静画。ダウンロードにはニコニコのアカウントが要る）:

- ずんだもん: https://seiga.nicovideo.jp/seiga/im10788496 （V3.2: https://seiga.nicovideo.jp/seiga/im11206626）
- 四国めたん: https://seiga.nicovideo.jp/seiga/im10791276

作者の条件は「公式の規約の範囲なら何に使ってもよい。クレジットは任意」。

### 権利（2026-09 に確認）

- **声**（VOICEVOX）: 商用・非商用とも可。クレジット `VOICEVOX:ずんだもん` / `VOICEVOX:四国めたん` を
  概要欄か動画内に書く（音源利用規約 https://zunko.jp/con_ongen_kiyaku.html ）。`brand.py` の
  `credit_line` が概要欄の先頭に入れる。
- **キャラクター絵**: 東北ずん子・ずんだもんプロジェクトのガイドライン https://zunko.jp/guideline.html 。
  「個人が自分のBlogやYoutubeに広告を出す、スーパーチャットを受け取る…程度は非商用の範囲」なので、
  個人チャンネルの収益化はこの範囲。(c) 表記は不要。グッズ販売など本来の商用は別途許諾。
- 四国めたんも同プロジェクトのキャラクターで、同じガイドラインの下にある。

### PSD から PNG を切り出す

手でレイヤーを切り替えて12回書き出すのではなく、`tools/sprite_export.py` に任せる。
全フレームを同じ枠で切るので、口パクで絵がずれない。

```bash
pip install psd-tools
python tools/sprite_export.py list  ~/Downloads/ずんだもん立ち絵.psd     # レイヤー構成を見る
python tools/sprite_export.py guess ~/Downloads/ずんだもん立ち絵.psd > config/sprites/zundamon.yaml
#   ↑ 下書き。list の出力と見比べて、目・眉・口・体のレイヤーを1つずつ指定する
python tools/sprite_export.py export ~/Downloads/ずんだもん立ち絵.psd assets/sprites/zundamon --recipe config/sprites/zundamon.yaml
```

レシピは `base`（毎フレーム表示：体・服・肌）、`expressions`（表情ごとの目と眉）、`mouth`（閉/開）
の3つ。レイヤーは `目/通常` のように `/` 区切りで指定し、一意ならレイヤー名だけでもよい。
PSDTool の `!` `*` 接頭辞は無視して照合する。

## illustrations/ — 図に置くイラスト

`place element=<ファイル名（拡張子なし）> slot=…` で台本から参照できる。いらすとや等のフリー素材を想定。

- いらすとやは **1作品につき20点まで無料**。21点以上は有償になるので、1動画で使う数は台本検証で止める（`assets.max_per_video`）。
- 透過PNG推奨。ファイル名は台本が書きやすい英小文字（`magnet.png`, `scientist.png`, `house.png`）。

## backgrounds/ — 背景

- `room.png`（部屋。雑談導入と締めで使う）、`space.jpg` など。`background name=<ファイル名>` で参照。
- NASA/ESA の写真はパブリックドメインまたは CC BY。写真は暗くして図の後ろに敷くだけなので、解像度は 1920×1080 あれば足りる。

## library.yaml

```yaml
- file: sprites/zundamon/normal_closed.png
  source: https://…
  licence: 東北ずん子・ずんだもんプロジェクト キャラクター利用規約
  credit: "VOICEVOX:ずんだもん"
- file: illustrations/magnet.png
  source: https://www.irasutoya.com/…
  licence: いらすとや 利用規定（1作品20点まで）
  credit: "イラスト: いらすとや"
```
