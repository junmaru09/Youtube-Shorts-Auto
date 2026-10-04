# 引き継ぎ資料 — Codespace から ローカル VS Code へ

このファイルは、**作業環境を GitHub Codespace から手元の VS Code（WSL2 Ubuntu）へ移すため**の資料。
人間が読む手順と、新しい Claude Code セッションが文脈を取り戻すための現状をまとめてある。

- リポジトリ: `junmaru09/Youtube-Shorts-Auto`（private）
- 作業ブランチ: **`feat/longform-pipeline`**（PR #2。`main` にはまだマージしていない）
- 2026-10-04 時点の HEAD: `fdd0678`

---

## 1. いま何を作っているか

ずんだもん（解説）と四国めたん（聞き手）が、**1枚の「板」に図を描き足しながら**宇宙・地球の話をする
15分前後の日本語解説動画を、自動で作るパイプライン。目標は手残り月3万円、生成費用は月5千円以内。

参考にしたチャンネルは rui_science（6本を実測）。計測で分かった本質は
**「画面全体は1〜3回しか切り替わらないが、板の中身は10秒に1回変わる」**こと。
だから映像は写真のモンタージュではなく、**1行のセリフ = 板への1手**で作る。

```
research → script → narrate → footage → assemble → review → publish → go-live → sync-stats → report
 題材と    章ごとに  VOICEVOX   NASA写真   板を1行ずつ   人間が    private   公開      計測
 出典      台本＋板書  で合成    （背景用）  描いて合成    承認      で投稿
```

---

## 2. 移行手順（WSL2 Ubuntu 上の VS Code）

Codespace 側に**残しておく必要のあるものは無い**。コードは全て push 済みで、
`work/`（DB・レンダリング結果・ログ）は `.gitignore` 対象＝元々あなたのマシン側が本体。

### 2.1 VS Code 側の準備

1. VS Code に拡張「**WSL**」を入れる（Microsoft 製）。
2. VS Code 左下の緑のアイコン → `Connect to WSL`。
3. `File > Open Folder` → `/home/junya/Youtube-Shorts-Auto`
   （すでにクローン済みのはず。無ければ `git clone https://github.com/junmaru09/Youtube-Shorts-Auto.git`）
4. 拡張「**Claude Code**」を入れる。WSL 側にインストールされる点に注意（ローカルではなく WSL に入れる）。
5. ターミナルは VS Code 内の WSL ターミナルを使う（`Ctrl+@`）。

### 2.2 最新を取り込む

```bash
cd ~/Youtube-Shorts-Auto
git status                       # 未コミットの変更が無いか確認
git pull origin feat/longform-pipeline
```

### 2.3 依存

```bash
sudo apt update && sudo apt install -y ffmpeg fonts-noto-cjk fonts-mplus p7zip-full python3-pip python3-venv
pip install -e ".[dev,review]"   # externally-managed と言われたら下の venv 手順
```

venv を使う場合（Ubuntu 24.04 では必要になることがある）:

```bash
python3 -m venv .venv
source .venv/bin/activate        # 以後、作業のたびに必要
pip install -e ".[dev,review]"
```

### 2.4 鍵と認証

- `.env`（`.gitignore` 済み・移行不要だがマシンに無ければ作る）:
  ```bash
  cp .env.example .env
  # ANTHROPIC_API_KEY= に自分のキーを書く
  ```
- YouTube 投稿用 OAuth は `config/oauth/`（`.gitignore` 済み）。投稿段階になってから。
- Google TTS は**使っていない**（VOICEVOX に移行済み）。`gcloud auth` は不要。

### 2.5 VOICEVOX

CPU 版エンジンを WSL2 内に展開済みのはず（`~/voicevox_engine/linux-cpu-x64/run`）。
`config/settings.yaml` の `tts.voicevox.engine_dir` がそこを指していれば、
`tube-auto narrate` が自動で起動・終了する。手動起動は不要。
詳細は [README の「初回セットアップ」](../README.md)。

### 2.6 確認

```bash
tube-auto doctor
```

`VOICEVOX` が OK、`sprites` が OK（立ち絵を置いてあるなら）、
`OAuth [ja]` の FAIL は投稿前なので無視してよい。

---

## 3. 何がどこにあるか

| 場所 | 中身 |
|---|---|
| `src/tube_auto/canvas/` | **板（図）の描画エンジン**。この動画の品質はほぼここで決まる |
| `src/tube_auto/canvas/manual.py` | 台本モデルに見せる「図の書き方」。操作・スロット・絵の一覧 |
| `src/tube_auto/canvas/dsl.py` | 台本が書く1行（`bell slot=left name=b`）を操作に変換 |
| `src/tube_auto/canvas/elements.py` | コードで描く図 26種（円グラフ・年表・散布図・天秤・波…） |
| `src/tube_auto/canvas/catalogue.py` | 絵の目録（パック単位）。いらすとや等の受け皿 |
| `src/tube_auto/stages/script.py` | 章ごとの台本生成と**検証ルール**。差し戻しの条件は全てここ |
| `src/tube_auto/stages/whiteboard.py` | 1行1フレームの描画、口パク、出現ハロー、章のクロスフェード |
| `src/tube_auto/stages/assemble.py` | 映像と音声の合成、BGM、締めカード |
| `src/tube_auto/quality.py` | 合成後の自動採点（変化/分・最長静止・字幕・落とした板書） |
| `src/tube_auto/brand.py` | チャンネルの定義。**章構成（EPISODE_PLAN）と話者**はここ |
| `assets/sprites/` | 立ち絵（坂本アヒル氏のPSDから切り出し済み、コミット済み） |
| `assets/illustrations/` + `assets/packs/` | 絵 216点（Noto Emoji）。パックを足せば増やせる |
| `config/settings.yaml` | 長さ・品質基準・TTS・BGM・予算 |
| `work/`（**git対象外**） | DB・音声・レンダリング結果・ログ・プレビュー |
| `tools/` | 補助。下記参照 |

### よく使う補助ツール

```bash
python tools/preview_script.py                 # 費用ゼロで板書を動画プレビュー
python tools/canvas_demo.py                    # 図のサンプル12枚
python tools/analyze_reference.py <動画>        # カット数・変化/分・静止時間を計測
python tools/add_pictures.py <フォルダ> --pack irasutoya   # 絵をパックに登録
python tools/sprite_export.py export <psd> assets/sprites/zundamon --recipe config/sprites/zundamon.yaml
python tools/fetch_illustrations.py            # Noto Emoji の絵を取得（追加したとき）
```

---

## 4. 1本作る手順

```bash
tube-auto research --count 1 --theme black_holes   # 題材と出典。テーマ省略可
tube-auto script                                   # 章ごとに9回。約$0.4
# → work/logs/script_idea0000N_preview.png で図を確認（音声合成の前に見られる）
tube-auto narrate --idea N                         # VOICEVOX。無料、数分
tube-auto footage --idea N                         # NASA写真（背景用）
tube-auto assemble --idea N                        # 合成＋自動採点＋サムネ3案
python tools/analyze_reference.py work/renders/idea_0000N.mp4
streamlit run review_app.py                        # 人間の承認
```

章単位の作り直し（安い）:

```bash
tube-auto script --idea N --chapter mechanism1     # 1章だけ。$0.05〜0.15
tube-auto requeue --idea N                         # 台本と音声を捨てて researched に戻す
tube-auto skip --idea N                            # そのアイデアを列から外す
```

---

## 5. 現状と次にやること

### 済んでいること

- 板書エンジン（図21操作・26要素・絵216点・衝突回避・出現ハロー・章クロスフェード）
- ずんだもん／四国めたんの立ち絵、向かい合わせ、口パク、表情5種
- VOICEVOX ナレーション（費用ゼロ）
- 章ごとの台本生成（差し戻しは章単位、直らなければ板書を落として採用＝動画は必ず出る）
- ノート紙テーマ（方眼・黄色タブ・紺の帯）＝参考チャンネルとの差別化
- 合成後の自動採点、レビュー画面への表示
- サムネ3案（顔＋巨大文字＋NASA写真）
- いらすとや等を足すためのパック機構（20点上限の自動チェック込み）

### 次にやること（優先順）

1. **いらすとやの絵を20点前後集めて登録**（`assets/README.md` に手順）。
   Emoji に無い「驚く人・考える人・望遠鏡をのぞく人・鐘をつく人」など**状況と表情**を狙う。
2. その状態で **idea 3（ブラックホールの鐘の音）の台本を作り直す**
   （`tube-auto requeue --idea 3` → `tube-auto script --idea 3 --reset-attempts`）。
   「文字での説明が多すぎる」「波とグラフばかり」への対策が入っているので、効果を確認する。
3. 動画にして `tools/analyze_reference.py` で計測。**合格ライン**はハードカット5未満、
   ステージ変化100回以上/15分、30秒超の静止なし（冒頭の部屋は75秒まで可）。
4. BGM を `work/bgm/<mood>/` に数曲ずつ（YouTube オーディオライブラリから手で）。
5. 投稿の準備（OAuth、`publish.channel_started_on`、ランプアップ）。

### 未解決・判断待ち

- 図の密度がまだ参考動画に届かない（実測2〜5回/分 vs 5〜13回/分）。
- 冒頭の掴みは改善したが、本題に入ってからの説明が文字寄りになりがち。
- `tube-auto diagrams` は旧 matplotlib 図解で**もう使わない**（CLI には残っているが assemble が無視する）。

---

## 6. 引っかかりやすい点（実際に踏んだもの）

| 症状 | 原因と対処 |
|---|---|
| `narrate` が「この台本には板書の指定が無い」と拒否 | 板書リライト前の古い台本。`requeue` してから `script` し直す |
| `script` が差し戻しを繰り返す | 検証ルールが厳しすぎる可能性。`work/logs/script_rejected_*.json` に生の応答が残るので、まずそれを読む |
| arXiv が 406 を返す | 向こう側の不調。リトライと `rss.arxiv.org` へのフォールバック実装済み。繰り返すならテーマを変える |
| `research` が「only 0 feed items」 | その日そのテーマの在庫切れ。`--theme cosmology` などで在庫の多いテーマを指定 |
| Windows 側のエクスプローラーで `\\wsl.localhost\...` に展開 | シンボリックリンクが壊れる。**展開は必ず WSL のターミナルで** |
| `tube-auto` が command not found | `pip install -e ".[dev]"`。venv を使っているなら `source .venv/bin/activate` |

---

## 7. 費用

| 段階 | 1本あたり |
|---|---|
| research | 約 $0.05 |
| script（章ごと9回＋差し戻し） | 約 $0.4 |
| narrate（VOICEVOX） | 0 |
| 画像生成 | 0（オフのまま。使わない方針） |
| **合計** | **約 $0.45（約70円）** |

月30本で約2,100円。上限は `config/settings.yaml` の `budget` と `tube-auto report` で監視。

---

## 8. 新しい Claude セッションへ

この資料と `README.md`、それに以下を読めば文脈は戻る:

- `src/tube_auto/canvas/manual.py` — 図の語彙（モデルに見せているもの）
- `src/tube_auto/stages/script.py` の `check_chapter` — 何を差し戻すか
- `src/tube_auto/brand.py` の `EPISODE_PLAN` — 動画の骨格
- `git log --oneline -30` — 各変更の理由はコミットメッセージに書いてある

**設計の約束**（破ると過去の失敗を繰り返す）:

1. 台本は**章ごと**に生成し、**章ごと**に差し戻す。全体を捨てて作り直さない（3回で$1.3溶かした）。
2. 検証で落ちた板書は `hold` に落として**台本は採用する**。動画が出ないことの方が悪い。
3. モデルの書き癖は、ルールを増やすのではなく**DSL と検証側で吸収**する。
4. 写真のモンタージュに戻さない。章カード・タイトルテロップも入れない（参考チャンネルに無い）。
5. 生成AIで絵を描かない（視聴者の評判と YouTube の方針の両方で不利）。
