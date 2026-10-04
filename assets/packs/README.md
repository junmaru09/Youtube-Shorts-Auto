# packs/ — 絵の出どころごとの目録

1ファイル＝1つの出どころ。`assets/illustrations/<file>.png` に置いた絵を、
台本から使える名前と日本語に結びつける。

```yaml
id: irasutoya
title: いらすとや
source: https://www.irasutoya.com/
licence: いらすとや ご利用規定（1つの作品につき20点まで無料、21点以上は有償）
credit: "イラスト: いらすとや"
max_per_video: 20          # 台本検証がこの数を超えたら差し戻す
pictures:
  surprised_person: [surprised_person, 驚く人]
  thinking_person:  [thinking_person, 考える人]
  temple_bell:      [temple_bell, 鐘つき]
```

- `pictures` のキーが台本の書く名前（`surprised_person slot=left`）。
- 値は `[ファイル名（拡張子なし）, 日本語]`。日本語は台詞との照合に使うので、
  視聴者が言いそうな語にする（「驚く人」「考える人」「望遠鏡をのぞく人」）。
- `max_per_video` があるパックは、1本で使える**種類数**が数えられ、超えると
  台本が差し戻される。同じ絵を何回置いても1点と数える（規約の数え方に合わせている）。
