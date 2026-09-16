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
- どちらのキャラも公式の立ち絵（坂本アヒル氏）が配布されている。**各キャラクターの利用規約を読み、クレジット（`VOICEVOX:ずんだもん` 等）を必ず入れる。** クレジット文は `brand.py` の `credit` にある。
- 立ち絵は PSD で口・目・眉がレイヤー分けされているので、口の開閉2枚を書き出す。

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
